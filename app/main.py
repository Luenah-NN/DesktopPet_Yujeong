# -*- coding: utf-8 -*-
import sys, os, random, time, math, json
from collections import deque
from pathlib import Path
from PyQt5 import QtCore, QtGui, QtWidgets

CHAR_NAME = "Yujeong"
BG_MODE   = "rembg"
BASE_DIR  = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))

DISPLAY_FPS   = 12
DISPLAY_DELAY = 1.0 / DISPLAY_FPS
MIN_FRAME_DELAY      = 40
INITIAL_SYNC_FRAMES  = 2
WINDOW_PAD           = 2
EDGE_MARGIN          = 10
FLOOR_MARGIN         = 2

GRAVITY              = 1.1
BOUNCE_K             = 0.78
BOUNCE_MAX           = 4
BOUNCE_MIN_VEL       = 3.5
BOUNCE_UP_VEL_FLOOR  = 11.0

FREE_BOUNCE_SPEED_TH = 7.5
FREE_BOUNCE_DAMP     = 0.78
FREE_BOUNCE_FRICTION = 0.985
FREE_BOUNCE_MIN_SPD  = 1.35

FOLLOW_JUMP_NEAR     = 60
FOLLOW_JUMP_HOLD     = 0.6
FOLLOW_FAST_DIST     = 400
FOLLOW_RUN_DIST      = 200

# ✅ 프리셋에 “더 크게 1.25” 추가
SCALE_PRESETS = [
    ("작게", 0.4),
    ("기본", 0.65),
    ("더 크게", 1.25),
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
        pet._snap_floor(use_global=True)
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

        # ✅ 멀티모니터 토글
        self.use_virtual_desktop = False

        self.scale_base = 0.65
        self.scale      = self.scale_base
        self.is_giant   = False
        self.giant_anim_timer = None

        self.raw_animations = {}
        self.animations     = {}
        self.anim_max_size  = {}
        self.scaled_max_size = {}
        self.anim_meta      = {}
        self.global_max_h   = 64

        self._predecode_all()
        self._rebuild_scaled_cache()

        self.current_action    = None
        self.current_frame_idx = 0
        self.next_frame_time   = time.monotonic()
        self._sync_frames_left = INITIAL_SYNC_FRAMES

        self.current_pix_w = 64
        self.current_pix_h = 64
        self.current_floor_h = self.global_max_h

        self.vx, self.vy = 0.0, 0.0
        self.dragging = False
        self.drag_offset = QtCore.QPoint(0, 0)
        self.press_pos = None
        self.drag_threshold = 6
        self.drag_trace = deque(maxlen=8)

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

        # ✅ 랜덤이동 점프 때 첫 프레임만 보여줄 플래그
        self.random_jump_static = False
        self.random_jump_static_until = 0.0

        # 먼지 효과
        self.dust_label = None
        self.dust_pix = None
        self.last_dust_time = 0.0

        self._make_menu()

        self.set_action("idle", force=True)

        desk = self._desktop_rect()
        sx = desk.x() + max(40, desk.width()//2 - self.width()//2)
        sy = desk.y() + 40
        self.move(sx, sy)
        self._snap_floor(use_global=True)

        self.tick = QtCore.QTimer(self)
        self.tick.timeout.connect(self.update_loop)
        self.tick.start(16)

    # ---------------------------
    # 데스크톱 rect (토글 반영)
    # ---------------------------
    def _desktop_rect(self):
        if self.use_virtual_desktop:
            return desktop_virtual_rect()
        scr = QtGui.QGuiApplication.screenAt(self.pos())
        if scr:
            return scr.availableGeometry()
        scr = QtWidgets.QApplication.primaryScreen()
        return scr.availableGeometry() if scr else QtCore.QRect(0,0,1920,1080)

    # ---------------------------
    # 디코딩
    # ---------------------------
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
                    scaled_list.append((spm, delay))
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
            w = pix.width(); h = pix.height()
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
            w = pm.width(); h = pm.height()
            max_w = max(max_w, w); max_h = max(max_h, h)
            frames.append(pm); delays.append(0.05)
        return frames, delays, max_w, max_h

    # ---------------------------
    # 바닥
    # ---------------------------
    def _floor_y(self, use_global=False):
        desk = self._desktop_rect()
        if use_global:
            h = self.global_max_h
        else:
            h = self.current_floor_h
        return desk.y() + desk.height() - h - FLOOR_MARGIN

    def _snap_floor(self, use_global=False):
        if self.free_bounce:
            return
        fy = self._floor_y(use_global=use_global)
        self.move(self.x(), fy)

    # ---------------------------
    # 메뉴
    # ---------------------------
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

        if action == self.act_follow:
            self.follow_mouse = not self.follow_mouse
            if self.follow_mouse:
                self.random_walk = False
                self.force_run_until = time.monotonic() + 0.8
        elif action == self.act_random:
            self.random_walk = not self.random_walk
            if self.random_walk:
                self.follow_mouse = False
        elif action == self.act_dance:
            if self.mode == "dance":
                self.mode = "normal"
                self.set_action("idle", force=True)
            else:
                self._exit_modes()
                self.mode = "dance"
                self.set_action("dance", force=True)
        elif action == self.act_ex:
            if self.mode == "exercise":
                self.mode = "normal"
                self.exercise_timer.stop()
                self.set_action("idle", force=True)
            else:
                self._exit_modes()
                self.mode = "exercise"
                self.exercise_idx = 0
                self.set_action(self.exercise_cycle[self.exercise_idx], force=True)
                self.exercise_timer.start(10_000)
        elif action == self.act_sleep:
            if self.mode == "sleep":
                self.mode = "normal"
                self.set_action("idle", force=True)
            else:
                self._exit_modes()
                self.mode = "sleep"
                self.set_action("sleep", force=True)
        elif action in self.size_actions:
            for act in self.size_actions:
                act.setChecked(False)
            action.setChecked(True)
            self.scale_base = action._scale_value
            if not self.is_giant:
                self._set_scale(self.scale_base)
            else:
                self._set_scale(self.scale_base * 3.0)
            self._snap_floor(use_global=True)
        elif action == self.act_giant:
            if self.is_giant:
                self._start_giant_anim(target=self.scale_base)
            else:
                self._start_giant_anim(target=self.scale_base * 3.0)
        elif action == self.act_multi:
            self.use_virtual_desktop = not self.use_virtual_desktop
            self._snap_floor(use_global=True)
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

    # ---------------------------
    # 스케일 / 거인화
    # ---------------------------
    def _set_scale(self, new_scale: float):
        self.scale = max(0.25, min(3.5, new_scale))
        self._rebuild_scaled_cache()
        if self.current_action:
            self._apply_current_frame()
        self._snap_floor(use_global=True)

    def _start_giant_anim(self, target: float, dur: float = 2.0):  # ✅ 2초
        self.giant_anim_target = target
        self.giant_anim_start  = self.scale
        self.giant_anim_start_t = time.monotonic()
        self.giant_anim_dur    = dur
        self.is_giant = target > self.scale_base + 1e-3
        if self.giant_anim_timer is None:
            self.giant_anim_timer = QtCore.QTimer(self)
            self.giant_anim_timer.timeout.connect(self._giant_anim_step)
        self.giant_anim_timer.start(40)

    def _giant_anim_step(self):
        now = time.monotonic()
        t = (now - self.giant_anim_start_t) / self.giant_anim_dur
        if t >= 1.0:
            self._set_scale(self.giant_anim_target)
            self.giant_anim_timer.stop()
            return
        s = self.giant_anim_start + (self.giant_anim_target - self.giant_anim_start) * t
        self._set_scale(s)

    # ---------------------------
    # 액션
    # ---------------------------
    def set_action(self, key, force=False):
        if self.mode in ("dance","sleep","exercise") and not force:
            return
        if not force and key == self.current_action:
            return
        if key not in self.animations:
            return

        self.current_action = key
        self.current_frame_idx = 0

        frames = self.animations[key]
        _, h = self.scaled_max_size.get(key, (self.current_pix_w, self.current_pix_h))
        self.current_floor_h = h

        global_bottom = self._floor_y(use_global=True)
        if self.y() > global_bottom:
            self.move(self.x(), global_bottom)

        if frames:
            self._apply_frame(frames[0][0])

        if key not in FLOOR_SNAP_EXCLUDE and not self.free_bounce:
            self._snap_floor(use_global=True)

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
        if not self.current_action:
            return

        # ✅ 랜덤 이동에서 건 점프면 여기서 프레임 넘김 막기
        if self.random_jump_static and self.current_action == "jump":
            if now < self.random_jump_static_until:
                return
            else:
                self.random_jump_static = False  # 다시 애니 가능

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

    # ---------------------------
    # temp
    # ---------------------------
    def _play_temp(self, key, ms, stop_during=False):
        self.temp_token += 1
        tok = self.temp_token
        self.active_temp_action = key
        self.set_action(key, force=True)
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
                self.set_action("idle", force=True)
        QtCore.QTimer.singleShot(ms, _end)

    # ---------------------------
    # 마우스
    # ---------------------------
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
                if self.mode not in ("dance","sleep","exercise"):
                    self.set_action("hang", force=True)
        if self.dragging:
            self._record_drag(ev.globalPos())
            self.move(ev.globalPos() - self.drag_offset)

    def mouseReleaseEvent(self, ev):
        if ev.button() != QtCore.Qt.LeftButton:
            return
        if self.dragging:
            self.dragging = False
            self._apply_throw_velocity()
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
        # ✅ 토글 모션일 때 던지기 금지
        if self.mode in ("dance","sleep","exercise"):
            self.free_bounce = False
            self.is_bouncing = False
            self.vx = 0.0; self.vy = 0.0
            self._snap_floor(use_global=True)
            return
        # ✅ 거인화일 때 던지기 금지
        if self.is_giant:
            self.free_bounce = False
            self.is_bouncing = False
            self.vx = 0.0; self.vy = 0.0
            self._snap_floor(use_global=True)
            return

        if len(self.drag_trace) < 2:
            return
        (p2, t2) = self.drag_trace[-1]
        (p1, t1) = self.drag_trace[0]
        dt = max(1e-3, (t2 - t1))
        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        fps = dt / 0.016
        self.vx = dx / max(1.0, fps)
        self.vy = dy / max(1.0, fps)
        speed = math.hypot(self.vx, self.vy)
        if speed >= FREE_BOUNCE_SPEED_TH:
            self.free_bounce = True
            self.is_bouncing = False
        else:
            self.free_bounce = False
            self.is_bouncing = True
            self.bounce_count = 0

    # ---------------------------
    # 운동
    # ---------------------------
    def _exercise_next(self):
        if self.mode != "exercise":
            self.exercise_timer.stop()
            return
        self.exercise_idx = (self.exercise_idx + 1) % len(self.exercise_cycle)
        self.set_action(self.exercise_cycle[self.exercise_idx], force=True)

    # ---------------------------
    # 먼지 효과
    # ---------------------------
    def _ensure_dust_sprite(self):
        if self.dust_label is not None:
            return
        self.dust_label = QtWidgets.QLabel(self)
        pm = QtGui.QPixmap(44, 16)
        pm.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(pm)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        color = QtGui.QColor(180, 180, 180, 140)
        painter.setBrush(color)
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawEllipse(2, 6, 14, 8)
        painter.drawEllipse(14, 7, 16, 7)
        painter.end()
        self.dust_pix = pm
        self.dust_label.setPixmap(pm)
        self.dust_label.hide()

    def _show_dust(self):
        self._ensure_dust_sprite()
        self.dust_label.move(4, self.height() - self.dust_label.height())
        self.dust_label.show()
        QtCore.QTimer.singleShot(180, self.dust_label.hide)

    # ---------------------------
    # 메인 루프
    # ---------------------------
    def update_loop(self):
        now = time.monotonic()
        self._update_animation(now)

        if self.active_temp_action and now > self.force_action_until:
            self.active_temp_action = None
            if self.mode == "normal":
                self.set_action("idle", force=True)

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

        # ✅ 거인화 상태에서는 바운스 없이 착지
        if self.is_giant:
            if not self.dragging and not in_climb:
                self.move(g.x(), self._floor_y(use_global=True))
                self.vy = 0.0
        else:
            if not self.dragging and not in_climb:
                if g.y() < bottom - 0.5:
                    self.vy += GRAVITY
                    ny = g.y() + int(self.vy)
                    if ny >= bottom:
                        if abs(self.vy) > BOUNCE_MIN_VEL and self.bounce_count < BOUNCE_MAX:
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

        # ---- 마우스 따라가기 ----
        if self.follow_mouse and not self.dragging and self.mode == "normal" and not self.active_temp_action:
            mp = QtGui.QCursor.pos()
            cx = g.x() + self.current_pix_w//2
            dist = abs(mp.x() - cx)
            global_bottom = self._floor_y(use_global=True)

            if dist <= FOLLOW_JUMP_NEAR:
                if now >= self.follow_force_jump_until:
                    self.follow_force_jump_until = now + FOLLOW_JUMP_HOLD
                if self.current_action != "jump":
                    self.set_action("jump", force=True)
                self.move(g.x(), global_bottom)
                return

            if now < self.follow_force_jump_until:
                if self.current_action != "jump":
                    self.set_action("jump", force=True)
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
                self.set_action(want)

            if self.is_giant and want in ("walk_left","walk_right","run_left","run_right"):
                if now - self.last_dust_time > 0.25:
                    self._show_dust()
                    self.last_dust_time = now

            return

        # ---- 랜덤 이동 ----
        if self.random_walk and not self.dragging and self.mode == "normal" and not self.active_temp_action:
            vx = getattr(self, "rw_vx", None)
            if vx is None or vx == 0:
                vx = random.choice([-2, -1, 1, 2])
            nx = g.x() + vx
            if nx <= left_edge:
                nx = left_edge
                vx = abs(vx)
            elif nx >= right_edge:
                nx = right_edge
                vx = -abs(vx)
            self.rw_vx = vx
            self.move(nx, self._floor_y(use_global=True))
            self.set_action("walk_right" if vx>0 else "walk_left")

            # ✅ 거인화 상태에서는 점프 안함
            if not self.is_giant:
                # ✅ 여기서 점프할 때는 jump 첫 프레임만 나오도록
                if random.random() < 0.007:
                    self.set_action("jump", force=True)
                    self.current_frame_idx = 0
                    self._apply_current_frame()
                    self.vy = -13.0
                    # ✅ 이 시간 동안은 _update_animation 이 점프 프레임을 넘기지 않게
                    self.random_jump_static = True
                    self.random_jump_static_until = now + 0.5  # 0.5초 정도 고정
            else:
                if now - self.last_dust_time > 0.25:
                    self._show_dust()
                    self.last_dust_time = now

            return

        # ---- idle ----
        if (not self.dragging
            and not self.active_temp_action
            and self.mode == "normal"
            and not self.follow_mouse
            and not self.random_walk):
            if self.current_action != "idle":
                self.set_action("idle")

    # ---------------------------
    # 당구 모드
    # ---------------------------
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
            self.vx = 0.0; self.vy = 0.0
            self._snap_floor(use_global=True)


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
