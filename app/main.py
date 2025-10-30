# -*- coding: utf-8 -*-
import sys, os, random, time, math
from collections import deque
from pathlib import Path
from PyQt5 import QtCore, QtGui, QtWidgets

CHAR_NAME = "Yujeong"
BG_MODE   = "rembg"
BASE_DIR  = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))

# 애니 기본 12fps (원래 20fps였던 거를 프레임 점프해서 보정하는 구조로 되어있음)
DISPLAY_FPS   = 12
DISPLAY_DELAY = 1.0 / DISPLAY_FPS
MIN_FRAME_DELAY      = 40
INITIAL_SYNC_FRAMES  = 2
WINDOW_PAD           = 2

EDGE_MARGIN   = 10
FLOOR_MARGIN  = 2

# 물리
GRAVITY              = 1.1
BOUNCE_K             = 0.78
BOUNCE_MAX           = 4        # ✅ 요청사항
BOUNCE_MIN_VEL       = 3.5
BOUNCE_UP_VEL_FLOOR  = 11.0     # ✅ 오타났던 부분 고정

# 당구(잡아던지기)
FREE_BOUNCE_SPEED_TH = 12.0
FREE_BOUNCE_DAMP     = 0.78
FREE_BOUNCE_FRICTION = 0.985
FREE_BOUNCE_MIN_SPD  = 1.35

# 마우스 따라가기
FOLLOW_JUMP_NEAR     = 60
FOLLOW_JUMP_HOLD     = 0.6
FOLLOW_FAST_DIST     = 400
FOLLOW_RUN_DIST      = 200

# 크기 프리셋 (요청: “더 크게” 삭제)
SCALE_PRESETS = [
    ("작게", 0.4),
    ("기본", 0.65),
    ("크게", 0.9),
]

ACTIONS = {
    "idle": "idle/idle.gif",
    "walk_left": "walk_left/walk_left.gif",
    "walk_right": "walk_right/walk_right.gif",
    "climb_left": "climb_left/climb_left.gif",
    "climb_right": "climb_right/climb_right.gif",
    "hang": "hang/hang.gif",
    "dance": "dance/dance.gif",
    "eat": "eat/eat.gif",
    "run_left": "run_left/run_left.gif",
    "run_right": "run_right/run_right.gif",
    "surprise": "surprise/surprise.gif",
    "angry": "angry/angry.gif",
    "pet": "pet/pet.gif",
    "jump": "jump/jump.gif",
    "squat": "squat/squat.gif",
    "boxing": "boxing/boxing.gif",
    "plank": "plank/plank.gif",
    "jumping_jacks": "jumping_jacks/jumping_jacks.gif",
    "sleep": "sleep/sleep.gif",
}

# 바닥에 딱 붙이지 않는 모션
FLOOR_SNAP_EXCLUDE = {"climb_left", "climb_right", "hang"}


def desktop_virtual_rect():
    app = QtWidgets.QApplication.instance()
    if app and app.primaryScreen():
        return app.primaryScreen().virtualGeometry()
    scr = QtWidgets.QApplication.primaryScreen()
    return scr.virtualGeometry() if scr else QtCore.QRect(0, 0, 1920, 1080)


class PetManager(QtCore.QObject):
    MAX_PETS = 16
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.pets = []

    def spawn(self, pos=None):
        if len(self.pets) >= self.MAX_PETS:
            return None
        pet = Pet(self)
        self.pets.append(pet)
        if pos is not None:
            pet.move(pos)
        pet._snap_floor_force()   # ✅ 처음 스폰도 force로
        pet.show()
        return pet

    def remove(self, pet):
        try:
            self.pets.remove(pet)
        except ValueError:
            pass
        pet.close()
        if not self.pets:
            QtCore.QTimer.singleShot(0, self.app.quit)


class Pet(QtWidgets.QMainWindow):
    def __init__(self, manager: PetManager):
        super().__init__()
        self.mgr = manager

        # 창 설정
        self.setWindowTitle(CHAR_NAME)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)
        self.setWindowFlag(QtCore.Qt.FramelessWindowHint, True)
        self.setWindowFlag(QtCore.Qt.Tool, True)
        self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, True)
        self.setContentsMargins(0, 0, 0, 0)
        self.setMouseTracking(True)

        icon_path = (BASE_DIR / "icons" / "icon.ico").as_posix()
        if os.path.exists(icon_path):
            self.setWindowIcon(QtGui.QIcon(icon_path))

        self.label = QtWidgets.QLabel(self)
        self.label.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.label.setContentsMargins(0, 0, 0, 0)
        self.setCentralWidget(self.label)

        # ✅ 멀티 모니터 토글
        self.use_virtual_desktop = False

        # 스케일
        self.scale_base = 0.65
        self.scale      = self.scale_base

        # ✅ 거인 모드 / 거인 애니 상태
        self.is_giant   = False
        self.giant_anim_timer = None
        self.giant_anim_target = self.scale_base
        self.giant_anim_start  = self.scale_base
        self.giant_anim_start_t = 0.0
        self.giant_anim_dur     = 0.5  # 요청: 0.5초
        self.giant_animating    = False
        self.giant_anim_pix     = None  # 확대 중일 때 고정 프레임

        # 애니 캐시
        self.raw_animations  = {}
        self.animations      = {}
        self.anim_max_size   = {}
        self.scaled_max_size = {}
        self.anim_meta       = {}
        self.global_max_h    = 64

        # 사전 디코딩
        self._predecode_all()
        self._rebuild_scaled_cache()

        # 현재 상태
        self.current_action    = None
        self.current_frame_idx = 0
        self.next_frame_time   = time.monotonic()
        self._sync_frames_left = INITIAL_SYNC_FRAMES
        self.current_pix_w     = 64
        self.current_pix_h     = 64
        self.current_floor_h   = self.global_max_h

        # 물리
        self.vx, self.vy = 0.0, 0.0
        self.dragging = False
        self.drag_offset = QtCore.QPoint(0, 0)
        self.press_pos   = None
        self.drag_threshold = 6
        self.drag_trace = deque(maxlen=8)
        self.stop_move  = False

        self.follow_mouse = False
        self.random_walk  = False
        self.mode         = "normal"
        self.menu_open    = False

        self.follow_force_jump_until = 0.0

        self.exercise_cycle = ["squat","boxing","plank","jumping_jacks"]
        self.exercise_idx   = 0
        self.exercise_timer = QtCore.QTimer(self)
        self.exercise_timer.timeout.connect(self._exercise_next)

        self.temp_token          = 0
        self.active_temp_action  = None
        self.force_action_until  = 0.0

        self.single_click_timer = QtCore.QTimer(self)
        self.single_click_timer.setSingleShot(True)
        self.single_click_timer.timeout.connect(self._trigger_single_click)

        self.climb_hold_until = 0.0
        self.climb_hold_timer = None
        self.follow_resume_dir = 0
        self.follow_resume_deadline = 0.0
        self.force_run_until = 0.0

        self.bounce_count  = 0
        self.is_bouncing   = False
        self.free_bounce   = False
        self.manual_drop   = False

        # 메뉴 만들기
        self._make_menu()

        # 초기 모션
        self.set_action("idle", force=True, suppress_bounce=True)

        # 시작 위치
        desk = self._desktop_rect()
        sx = desk.x() + max(40, desk.width()//2 - self.width()//2)
        sy = desk.y() + 40
        self.move(sx, sy)
        self._snap_floor_force()

        # 메인 루프
        self.tick = QtCore.QTimer(self)
        self.tick.timeout.connect(self.update_loop)
        self.tick.start(16)

    # ------------------------------------------------------------
    # 화면 rect
    # ------------------------------------------------------------
    def _desktop_rect(self):
        if self.use_virtual_desktop:
            return desktop_virtual_rect()
        scr = QtGui.QGuiApplication.screenAt(self.pos())
        if scr:
            return scr.availableGeometry()
        scr = QtWidgets.QApplication.primaryScreen()
        return scr.availableGeometry() if scr else QtCore.QRect(0,0,1920,1080)

    # ------------------------------------------------------------
    # 디코딩
    # ------------------------------------------------------------
    def _predecode_all(self):
        base = BASE_DIR / "assets" / CHAR_NAME
        for action, rel in ACTIONS.items():
            gif_path = base / rel
            if gif_path.exists():
                frames, delays, mw, mh = self._decode_gif(str(gif_path))
            else:
                png_dir = gif_path.parent
                frames, delays, mw, mh = self._decode_png_folder(png_dir)
            self.raw_animations[action] = list(zip(frames, delays))
            self.anim_max_size[action] = (mw, mh)
            if delays:
                avg_delay = sum(delays)/len(delays)
            else:
                avg_delay = 0.05
            if avg_delay <= 0:
                avg_delay = 0.05
            self.anim_meta[action] = {
                "avg_delay": avg_delay,
                "orig_fps": 1.0/avg_delay
            }

    def _rebuild_scaled_cache(self):
        self.animations = {}
        self.scaled_max_size = {}
        max_h_all = 1
        for action, raw_list in self.raw_animations.items():
            scaled_list = []
            max_w_raw, max_h_raw = self.anim_max_size.get(action, (64,64))
            max_w_s = max(1, int(max_w_raw * self.scale))
            max_h_s = max(1, int(max_h_raw * self.scale))
            for (pm, delay) in raw_list:
                if pm.isNull():
                    spm = QtGui.QPixmap(32, 32)
                    spm.fill(QtCore.Qt.transparent)
                else:
                    sw = max(1, int(pm.width() * self.scale))
                    sh = max(1, int(pm.height() * self.scale))
                    spm = pm.scaled(sw, sh, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                scaled_list.append((spm, delay))
            self.animations[action] = scaled_list
            self.scaled_max_size[action] = (max_w_s, max_h_s)
            max_h_all = max(max_h_all, max_h_s)
        self.global_max_h = max_h_all

    def _decode_gif(self, path):
        movie = QtGui.QMovie(path)
        frames, delays = [], []
        max_w = 1; max_h = 1
        idx = 0
        while True:
            if not movie.jumpToFrame(idx):
                break
            pix = movie.currentPixmap()
            if pix.isNull():
                break
            w, h = pix.width(), pix.height()
            max_w = max(max_w, w); max_h = max(max_h, h)
            frames.append(pix)
            d = movie.nextFrameDelay()
            if d <= 0:
                d = MIN_FRAME_DELAY
            delays.append(d/1000.0)
            idx += 1
        if not frames:
            pm = QtGui.QPixmap(64,64); pm.fill(QtCore.Qt.transparent)
            return [pm], [0.05], 64, 64
        return frames, delays, max_w, max_h

    def _decode_png_folder(self, folder: Path):
        if not folder.exists():
            pm = QtGui.QPixmap(64,64); pm.fill(QtCore.Qt.transparent)
            return [pm], [0.05], 64, 64
        files = sorted([p for p in folder.iterdir() if p.suffix.lower() in (".png",".webp",".jpg",".jpeg")],
                       key=lambda p: p.name)
        if not files:
            pm = QtGui.QPixmap(64,64); pm.fill(QtCore.Qt.transparent)
            return [pm], [0.05], 64, 64
        frames, delays = [], []
        max_w = 1; max_h = 1
        for p in files:
            pm = QtGui.QPixmap(p.as_posix())
            if pm.isNull():
                continue
            w, h = pm.width(), pm.height()
            max_w = max(max_w, w); max_h = max(max_h, h)
            frames.append(pm); delays.append(0.05)
        return frames, delays, max_w, max_h

    # ------------------------------------------------------------
    # 바닥
    # ------------------------------------------------------------
    def _floor_y(self, use_global=False):
        desk = self._desktop_rect()
        if use_global:
            h = self.global_max_h
        else:
            h = self.current_floor_h
        floor = desk.y() + desk.height() - h - FLOOR_MARGIN
        if floor < desk.y():
            floor = desk.y()
        return floor

    def _snap_floor(self, use_global=False):
        if self.free_bounce or self.manual_drop:
            return
        fy = self._floor_y(use_global=use_global)
        self.move(self.x(), fy)

    def _snap_floor_force(self):
        """무조건 바닥에 맞춘다 (프리셋/거인화 직후 등)."""
        fy = self._floor_y(use_global=True)
        self.move(self.x(), fy)
        self.manual_drop = False
        self.free_bounce = False
        self.vy = 0.0
        self.bounce_count = 0

    # ------------------------------------------------------------
    # 메뉴
    # ------------------------------------------------------------
    def _make_menu(self):
        self.menu = QtWidgets.QMenu(self)
        self.act_follow = self.menu.addAction("마우스 따라가기")
        self.act_random = self.menu.addAction("랜덤 이동")
        self.menu.addSeparator()
        self.act_dance  = self.menu.addAction("춤추기 (토글)")
        self.act_eat    = self.menu.addAction("간식주기 (6초)")
        self.act_pet    = self.menu.addAction("쓰다듬기 (6초)")
        self.act_ex     = self.menu.addAction("운동하기 (토글)")
        self.act_sleep  = self.menu.addAction("잠자기 (토글)")
        self.menu.addSeparator()
        self.size_menu  = self.menu.addMenu("크기")
        self.size_actions = []
        for name, sc in SCALE_PRESETS:
            act = self.size_menu.addAction(name)
            act.setCheckable(True)
            act._scale_value = sc
            self.size_actions.append(act)
        self.menu.addSeparator()
        self.act_giant  = self.menu.addAction("거인화 (토글)")
        self.act_giant.setCheckable(True)
        self.act_multi  = self.menu.addAction("멀티 모니터 이동 (토글)")
        self.menu.addSeparator()
        self.act_spawn  = self.menu.addAction("펫 추가")
        self.act_close  = self.menu.addAction("이 펫 닫기")

        for a in [self.act_follow, self.act_random,
                  self.act_dance, self.act_ex, self.act_sleep]:
            a.setCheckable(True)
        self.act_multi.setCheckable(True)

    def contextMenuEvent(self, ev):
        self.menu_open = True
        action = self.menu.exec_(self.mapToGlobal(ev.pos()))
        self.menu_open = False
        now = time.monotonic()

        if action == self.act_follow:
            self.follow_mouse = not self.follow_mouse
            if self.follow_mouse:
                self.random_walk = False
                self.force_run_until = now + 0.8

        elif action == self.act_random:
            self.random_walk = not self.random_walk
            if self.random_walk:
                self.follow_mouse = False

        elif action == self.act_dance:
            if self.mode == "dance":
                self.mode = "normal"
                self.set_action("idle", force=True, suppress_bounce=True)
            else:
                self._exit_modes()
                self.mode = "dance"
                self.set_action("dance", force=True, suppress_bounce=True)

        elif action == self.act_ex:
            if self.mode == "exercise":
                self.mode = "normal"
                self.exercise_timer.stop()
                self.set_action("idle", force=True, suppress_bounce=True)
            else:
                self._exit_modes()
                self.mode = "exercise"
                self.exercise_idx = 0
                self.set_action(self.exercise_cycle[self.exercise_idx],
                                force=True, suppress_bounce=True)
                self.exercise_timer.start(10_000)

        elif action == self.act_sleep:
            if self.mode == "sleep":
                self.mode = "normal"
                self.set_action("idle", force=True, suppress_bounce=True)
            else:
                self._exit_modes()
                self.mode = "sleep"
                self.set_action("sleep", force=True, suppress_bounce=True)

        elif action in self.size_actions:
            for act in self.size_actions:
                act.setChecked(False)
            action.setChecked(True)
            self.scale_base = action._scale_value
            if not self.is_giant:
                self._set_scale(self.scale_base)
            else:
                # 거인화 중일 때 프리셋 바꾸면 그 비율로 같이
                self._set_scale(self.scale_base * 4.0)
            # ✅ 프리셋 직후 taskbar 겹침 방지
            self._snap_floor_force()

        elif action == self.act_giant:
            if self.is_giant:
                # 줄이기
                self._start_giant_anim(target=self.scale_base, dur=0.5)
            else:
                # 커지기 (요청: 4배)
                self._start_giant_anim(target=self.scale_base * 4.0, dur=0.5)

        elif action == self.act_multi:
            self.use_virtual_desktop = not self.use_virtual_desktop
            self._snap_floor_force()

        elif action == self.act_eat:
            self._exit_modes()
            self._play_temp("eat", 6000)

        elif action == self.act_pet:
            self._exit_modes()
            self._play_temp("pet", 6000)

        elif action == self.act_spawn:
            g = self.geometry()
            self.mgr.spawn(pos=QtCore.QPoint(g.x()+50, g.y()+20))

        elif action == self.act_close:
            self.mgr.remove(self)

        self._refresh_menu_checks()

    def _refresh_menu_checks(self):
        self.act_follow.setChecked(self.follow_mouse)
        self.act_random.setChecked(self.random_walk)
        self.act_dance.setChecked(self.mode == "dance")
        self.act_ex.setChecked(self.mode == "exercise")
        self.act_sleep.setChecked(self.mode == "sleep")
        self.act_giant.setChecked(self.is_giant)
        self.act_multi.setChecked(self.use_virtual_desktop)

    def _exit_modes(self):
        if self.mode == "exercise":
            self.exercise_timer.stop()
        self.mode = "normal"

    # ------------------------------------------------------------
    # 스케일 / 거인화
    # ------------------------------------------------------------
    def _set_scale(self, new_scale: float):
        self.scale = max(0.25, min(5.5, new_scale))
        self._rebuild_scaled_cache()
        if self.current_action:
            self._apply_current_frame()

    def _start_giant_anim(self, target: float, dur: float = 0.5):
        # ✅ 현재 프레임을 하나 뽑아서 그걸로만 확대/축소
        base_pix = None
        if self.current_action in self.animations and self.animations[self.current_action]:
            base_pix = self.animations[self.current_action][self.current_frame_idx][0]
        else:
            base_pix = self.label.pixmap()
        if base_pix is None:
            base_pix = QtGui.QPixmap(self.width(), self.height())
            base_pix.fill(QtCore.Qt.transparent)

        self.giant_anim_pix     = base_pix
        self.giant_animating    = True
        self.giant_anim_target  = target
        self.giant_anim_start   = self.scale
        self.giant_anim_start_t = time.monotonic()
        self.giant_anim_dur     = dur
        self.is_giant           = target > self.scale_base + 1e-3

        if self.giant_anim_timer is None:
            self.giant_anim_timer = QtCore.QTimer(self)
            self.giant_anim_timer.timeout.connect(self._giant_anim_step)
        self.giant_anim_timer.start(20)

    def _giant_anim_step(self):
        now = time.monotonic()
        t = (now - self.giant_anim_start_t) / self.giant_anim_dur
        if t >= 1.0:
            # 끝
            self.giant_animating = False
            self._set_scale(self.giant_anim_target)
            # 끝난 뒤에 스냅
            self._snap_floor_force()
            self._refresh_menu_checks()
            if self.giant_anim_timer:
                self.giant_anim_timer.stop()
            return

        # 0~1
        s = self.giant_anim_start + (self.giant_anim_target - self.giant_anim_start) * t
        # 현재 프레임만 s 비율로 스케일
        pm = self.giant_anim_pix
        sw = max(1, int(pm.width()  * (s / self.scale)))  # 현재 scale 대비
        sh = max(1, int(pm.height() * (s / self.scale)))
        spm = pm.scaled(sw, sh, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        self.label.setPixmap(spm)
        self.label.resize(spm.width(), spm.height())
        self.setFixedSize(spm.width()+WINDOW_PAD, spm.height()+WINDOW_PAD)
        # 거인화 중에도 taskbar 안덮치게
        self._snap_floor_force()

    # ------------------------------------------------------------
    # 액션
    # ------------------------------------------------------------
    def set_action(self, key, force=False, suppress_bounce=True):
        # 거인화 중이면 모션 바꾸지 않음
        if self.giant_animating and not force:
            return
        if self.mode in ("dance","sleep","exercise") and not force:
            return
        if not force and key == self.current_action:
            return
        if key not in self.animations:
            return

        self.current_action = key
        self.current_frame_idx = 0

        _, h = self.scaled_max_size.get(key, (self.current_pix_w, self.current_pix_h))
        self.current_floor_h = h

        frames = self.animations[key]
        if frames:
            self._apply_frame(frames[0][0])

        if suppress_bounce:
            self.vy = 0.0
            self.bounce_count = 0
            self.free_bounce = False
            self.manual_drop = False

        if key not in FLOOR_SNAP_EXCLUDE and not self.free_bounce and not self.manual_drop:
            self._snap_floor()

    def _apply_frame(self, pix: QtGui.QPixmap):
        self.label.setPixmap(pix)
        dpr = pix.devicePixelRatio() or 1.0
        self.current_pix_w = int(pix.width()/dpr)
        self.current_pix_h = int(pix.height()/dpr)
        self.label.resize(self.current_pix_w, self.current_pix_h)
        self.setFixedSize(self.current_pix_w + WINDOW_PAD,
                          self.current_pix_h + WINDOW_PAD)
        if BG_MODE == "chroma":
            self.setMask(pix.createMaskFromColor(QtGui.QColor(255,255,255), QtCore.Qt.MaskOutColor))
        else:
            self.clearMask()

    def _apply_current_frame(self):
        frames = self.animations.get(self.current_action)
        if not frames:
            return
        self._apply_frame(frames[self.current_frame_idx][0])

    def _update_animation(self, now: float):
        if self.giant_animating:
            return  # 거인화 중에는 프레임 안넘김
        if not self.current_action:
            return
        frames = self.animations.get(self.current_action)
        if not frames:
            return
        if now < self.next_frame_time:
            return
        meta = self.anim_meta.get(self.current_action, {"orig_fps": 20.0})
        orig_fps = meta.get("orig_fps", 20.0)
        frame_step = max(1, round(orig_fps / DISPLAY_FPS))
        self.current_frame_idx = (self.current_frame_idx + frame_step) % len(frames)
        pix, _ = frames[self.current_frame_idx]
        self._apply_frame(pix)
        self.next_frame_time = now + DISPLAY_DELAY

    # ------------------------------------------------------------
    # temp
    # ------------------------------------------------------------
    def _play_temp(self, key, ms, stop_during=False):
        self.temp_token += 1
        tok = self.temp_token
        self.active_temp_action = key
        self.set_action(key, force=True, suppress_bounce=True)
        if stop_during:
            self.stop_move = True
        self.force_action_until = time.monotonic() + (ms / 1000.0)

        def _end():
            if tok != self.temp_token:
                return
            self.active_temp_action = None
            self.force_action_until = 0.0
            if stop_during:
                self.stop_move = False
            if self.mode == "normal":
                self.set_action("idle", force=True, suppress_bounce=True)
        QtCore.QTimer.singleShot(ms, _end)

    # ------------------------------------------------------------
    # 마우스
    # ------------------------------------------------------------
    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            interval = QtWidgets.QApplication.instance().doubleClickInterval()
            self.single_click_timer.start(interval)
            self.press_pos = ev.globalPos()
            self.dragging = False
            self.drag_offset = ev.globalPos() - self.frameGeometry().topLeft()
            self.drag_trace.clear()

    def mouseMoveEvent(self, ev):
        if self.press_pos is None:
            return
        if not self.dragging:
            if (ev.globalPos() - self.press_pos).manhattanLength() >= self.drag_threshold:
                self.single_click_timer.stop()
                self.dragging = True
                # 토글 모션 중이라도 드래그는 되게, 대신 hang은 안나오게 하고싶으면 여기서 조건 달면 됨
                self.set_action("hang", force=True, suppress_bounce=False)
        if self.dragging:
            self._record_drag(ev.globalPos())
            self.move(ev.globalPos() - self.drag_offset)

    def mouseReleaseEvent(self, ev):
        if ev.button() != QtCore.Qt.LeftButton:
            return
        if self.dragging:
            self.dragging = False
            self._apply_throw_velocity()
            # 던진 다음엔 그 자리에서 떨어지게
            if not self.free_bounce:
                self.manual_drop = True
                self.vy = 0.0
                self.bounce_count = 0
            self.press_pos = None
            return
        self.press_pos = None

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            self.single_click_timer.stop()
            self._do_double_click()

    def _trigger_single_click(self):
        self._do_single_click()

    def _do_single_click(self):
        if self.mode in ("dance","sleep","exercise"):
            return
        self._play_temp("surprise", 6000, stop_during=False)

    def _do_double_click(self):
        if self.mode in ("dance","sleep","exercise"):
            return
        self._play_temp("angry", 6000, stop_during=False)

    def _record_drag(self, gpos: QtCore.QPoint):
        self.drag_trace.append((QtCore.QPoint(gpos), time.monotonic()))

    def _apply_throw_velocity(self):
        # 토글 / 거인화 중엔 집어던지기 금지
        if self.mode in ("dance","sleep","exercise"):
            self.free_bounce = False
            self.vx = self.vy = 0.0
            return
        if self.is_giant:
            self.free_bounce = False
            self.vx = self.vy = 0.0
            return

        if len(self.drag_trace) < 2:
            return
        (p2, t2) = self.drag_trace[-1]
        (p1, t1) = self.drag_trace[0]
        dt = max(1e-3, (t2 - t1))
        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        frames = dt / 0.016
        self.vx = dx / max(1.0, frames)
        self.vy = dy / max(1.0, frames)
        speed = math.hypot(self.vx, self.vy)

        if speed >= FREE_BOUNCE_SPEED_TH:
            self.free_bounce = True
            self.manual_drop = False
        else:
            self.free_bounce = False
            self.manual_drop = True
            self.bounce_count = 0

    # ------------------------------------------------------------
    # 운동
    # ------------------------------------------------------------
    def _exercise_next(self):
        if self.mode != "exercise":
            self.exercise_timer.stop()
            return
        self.exercise_idx = (self.exercise_idx + 1) % len(self.exercise_cycle)
        self.set_action(self.exercise_cycle[self.exercise_idx], force=True, suppress_bounce=True)

    # ------------------------------------------------------------
    # 메인 루프
    # ------------------------------------------------------------
    def update_loop(self):
        now = time.monotonic()
        self._update_animation(now)

        if self.active_temp_action and now > self.force_action_until:
            self.active_temp_action = None
            if self.mode == "normal":
                self.set_action("idle", force=True, suppress_bounce=True)

        if self.free_bounce:
            self._update_free_bounce()
            return

        if self.menu_open:
            return

        desk = self._desktop_rect()
        g = self.geometry()
        left_edge  = desk.x()
        right_edge = desk.x() + desk.width() - self.current_pix_w
        bottom     = self._floor_y()

        in_climb = self.current_action in ("climb_left","climb_right")

        # ----------- 낙하/바운스 -----------
        if not self.dragging and not in_climb:
            if self.manual_drop:
                self.vy += GRAVITY
                ny = g.y() + int(self.vy)
                if ny >= bottom:
                    if (abs(self.vy) > BOUNCE_MIN_VEL and
                        self.bounce_count < BOUNCE_MAX):
                        self.vy = -abs(self.vy) * BOUNCE_K
                        if self.vy > -BOUNCE_UP_VEL_FLOOR:
                            self.vy = -BOUNCE_UP_VEL_FLOOR
                        self.bounce_count += 1
                        ny = bottom - 1
                    else:
                        ny = bottom
                        self.vy = 0.0
                        self.bounce_count = 0
                        self.manual_drop = False
                    self.move(g.x(), ny)
                else:
                    self.move(g.x(), ny)
            else:
                if g.y() < bottom:
                    self.vy += GRAVITY
                    ny = g.y() + int(self.vy)
                    if ny >= bottom:
                        if (abs(self.vy) > BOUNCE_MIN_VEL and
                            self.bounce_count < BOUNCE_MAX):
                            self.vy = -abs(self.vy) * BOUNCE_K
                            if self.vy > -BOUNCE_UP_VEL_FLOOR:
                                self.vy = -BOUNCE_UP_VEL_FLOOR
                            self.bounce_count += 1
                            ny = bottom - 1
                        else:
                            ny = bottom
                            self.vy = 0.0
                            self.bounce_count = 0
                        self.move(g.x(), ny)
                    else:
                        self.move(g.x(), ny)
                else:
                    self.vy = 0.0
                    self.move(g.x(), bottom)

        # ----------- 마우스 따라가기 -----------
        if self.follow_mouse and not self.dragging and self.mode == "normal" and not self.active_temp_action:
            mp = QtGui.QCursor.pos()
            cx = g.x() + self.current_pix_w//2
            dist = abs(mp.x() - cx)
            global_bottom = self._floor_y(use_global=True)

            if dist <= FOLLOW_JUMP_NEAR:
                if now >= self.follow_force_jump_until:
                    self.follow_force_jump_until = now + FOLLOW_JUMP_HOLD
                if self.current_action != "jump":
                    self.set_action("jump", force=True, suppress_bounce=False)
                self.move(g.x(), global_bottom)
                return

            if now < self.follow_force_jump_until:
                if self.current_action != "jump":
                    self.set_action("jump", force=True, suppress_bounce=False)
                self.move(g.x(), global_bottom)
                return

            dx = mp.x() - cx
            speed = 6 if (now < self.force_run_until or dist > FOLLOW_FAST_DIST) else 3
            step = speed if dx > 0 else -speed
            nx = max(left_edge, min(right_edge, g.x() + step))
            self.move(nx, global_bottom)

            if dist > FOLLOW_RUN_DIST:
                want = "run_right" if dx > 0 else "run_left"
            else:
                want = "walk_right" if dx > 0 else "walk_left"
            if want != self.current_action:
                self.set_action(want, suppress_bounce=False)
            return

        # ----------- 랜덤 이동 (점프 롤백된 버전) -----------
        if self.random_walk and not self.dragging and self.mode == "normal" and not self.active_temp_action:
            bottom_g = self._floor_y(use_global=True)
            vx = getattr(self, "rw_vx", None)
            if vx is None or vx == 0:
                vx = random.choice([-2, -1, 1, 2])
            nx = self.x() + vx
            hit_left = False
            hit_right = False
            if nx <= left_edge:
                nx = left_edge; vx = abs(vx); hit_left = True
            elif nx >= right_edge:
                nx = right_edge; vx = -abs(vx); hit_right = True

            self.rw_vx = vx
            self.move(nx, bottom_g)
            self.set_action("walk_right" if vx>0 else "walk_left", suppress_bounce=False)

            if hit_left:
                self.set_action("climb_left", force=True, suppress_bounce=False)
                return
            if hit_right:
                self.set_action("climb_right", force=True, suppress_bounce=False)
                return
            return

        # ----------- 기본 idle -----------
        if (not self.dragging
            and not self.active_temp_action
            and self.mode == "normal"
            and not self.follow_mouse
            and not self.random_walk):
            if self.current_action != "idle":
                self.set_action("idle", suppress_bounce=False)

    # ------------------------------------------------------------
    # free-bounce
    # ------------------------------------------------------------
    def _update_free_bounce(self):
        desk = self._desktop_rect()
        g = self.geometry()
        nx = g.x() + int(self.vx)
        ny = g.y() + int(self.vy)

        if nx <= desk.x():
            nx = desk.x()
            self.vx = -self.vx * FREE_BOUNCE_DAMP
        elif nx + self.width() >= desk.x() + desk.width():
            nx = desk.x() + desk.width() - self.width()
            self.vx = -self.vx * FREE_BOUNCE_DAMP

        if ny <= desk.y():
            ny = desk.y()
            self.vy = -self.vy * FREE_BOUNCE_DAMP
        elif ny + self.height() >= desk.y() + desk.height():
            ny = desk.y() + desk.height() - self.height()
            self.vy = -self.vy * FREE_BOUNCE_DAMP

        self.move(nx, ny)
        self.vx *= FREE_BOUNCE_FRICTION
        self.vy *= FREE_BOUNCE_FRICTION
        speed = math.hypot(self.vx, self.vy)
        if speed < FREE_BOUNCE_MIN_SPD:
            self.free_bounce = False
            self.vx = 0.0
            self.vy = 0.0
            self.bounce_count = 0
            # free-bounce 끝나면 그 자리에서 떨어지게
            self.manual_drop = True


def main():
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)
    if hasattr(QtCore.Qt, "HighDpiScaleFactorRoundingPolicy"):
        QtWidgets.QApplication.setHighDpiScaleFactorRoundingPolicy(
            QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    app = QtWidgets.QApplication(sys.argv)
    mgr = PetManager(app)
    mgr.spawn()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
