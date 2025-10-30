# -*- coding: utf-8 -*-
import sys, os, random, time
from collections import deque
from pathlib import Path
from PyQt5 import QtCore, QtGui, QtWidgets

# ===== 전역 설정 =====
CHAR_NAME = "Yujeong"
BG_MODE   = "rembg"     # "chroma" or "rembg"
BASE_DIR  = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))

MIN_FRAME_DELAY = 40
INITIAL_SYNC_FRAMES = 4
WINDOW_PAD   = 2          # 창이 픽스맵보다 살짝 큰 여유
EDGE_MARGIN  = 10
FLOOR_MARGIN = 2
GRAVITY      = 1.1
BOUNCE_K     = 0.9        # 튕김 크게
THROW_ANGRY_SPEED = 1200.0

# 마우스 따라가기 점프
FOLLOW_JUMP_NEAR = 60       # 커서 근접 범위
FOLLOW_JUMP_COOLDOWN = 0.8  # (커서가 움직일 때) 점프 간격
FOLLOW_JUMP_HOLD = 0.6      # 점프 한 번 유지되는 시간
FOLLOW_RUN_DIST  = 200
FOLLOW_FAST_DIST = 400
CURSOR_STILL_EPS = 3        # 이만큼 이하면 "커서 안 움직임"으로 판단

# 해상도 프리셋
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

# 바닥 붙임 제외 모션
FLOOR_SNAP_EXCLUDE = {"climb_left", "climb_right", "hang"}


def available_geo(window: QtWidgets.QWidget) -> QtCore.QRect:
    win = window.windowHandle()
    if win and win.screen():
        return win.screen().availableGeometry()
    scr = QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos())
    return scr.availableGeometry() if scr else QtWidgets.QApplication.primaryScreen().availableGeometry()


# =====================================================
# PetManager
# =====================================================
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
        pet._snap_floor()
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


# =====================================================
# Pet
# =====================================================
class Pet(QtWidgets.QMainWindow):
    def __init__(self, manager: PetManager):
        super().__init__()
        self.mgr = manager

        # 창
        self.setWindowTitle(CHAR_NAME)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)
        self.setContentsMargins(0, 0, 0, 0)
        self.setWindowFlag(QtCore.Qt.FramelessWindowHint, True)
        self.setWindowFlag(QtCore.Qt.Tool, True)
        self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, True)
        self.setMouseTracking(True)

        icon_path = (BASE_DIR / "icons" / "icon.ico").as_posix()
        if os.path.exists(icon_path):
            self.setWindowIcon(QtGui.QIcon(icon_path))

        # 라벨
        self.label = QtWidgets.QLabel(self)
        self.label.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.label.setContentsMargins(0, 0, 0, 0)
        self.label.setScaledContents(False)
        self.label.setMouseTracking(True)
        self.setCentralWidget(self.label)

        # 현재 프레임 실제 크기(스케일 후)
        self.current_pix_w = 64
        self.current_pix_h = 64

        # 스케일
        self.scale = 0.65

        # 상태/물리
        self.vx, self.vy = 0.0, 0.0
        self.dragging = False
        self.drag_offset = QtCore.QPoint(0, 0)
        self.press_pos = None
        self.drag_threshold = 6

        self.follow_mouse = False
        self.random_walk  = False
        self.stop_move    = False
        self.mode         = "normal"    # "dance"/"sleep"/"exercise" 시 잠금
        self.menu_open    = False

        # 점프 상태
        self.jump_cooldown_until = 0.0    # 커서 움직일 때만 사용
        self.jump_hold_until = 0.0        # 점프 유지 종료시간

        # 커서 위치 기록 (follow에서 “안 움직임” 판단용)
        self.last_cursor_pos = QtCore.QPoint(0, 0)
        self.last_cursor_t = 0.0

        # 운동 모드
        self.exercise_cycle = ["squat","boxing","plank","jumping_jacks"]
        self.exercise_idx = 0
        self.exercise_timer = QtCore.QTimer(self)
        self.exercise_timer.timeout.connect(self._exercise_next)

        # 임시 모션
        self.force_action_until = 0.0
        self.temp_token = 0
        self.active_temp_action = None  # surprise/angry일 때만

        # 클릭 타이머
        self.single_click_timer = QtCore.QTimer(self)
        self.single_click_timer.setSingleShot(True)
        self.single_click_timer.timeout.connect(self._trigger_single_click)

        # climb 관련
        self.climb_hold_until = 0.0
        self.climb_hold_timer = None
        self.follow_resume_dir = 0
        self.follow_resume_deadline = 0.0
        self.force_run_until = 0.0

        # 사전 디코딩
        self.raw_animations = {}
        self.animations     = {}
        self.anim_max_size  = {}

        self._predecode_all()
        self._rebuild_scaled_cache()

        # 현재 애니
        self.current_action = None
        self.current_frame_idx = 0
        self.next_frame_time = time.monotonic()
        self._sync_frames_left = INITIAL_SYNC_FRAMES

        # 메뉴
        self._make_menu()

        # 초기 모션
        self.set_action("idle", force=True)

        # 메인 틱
        self.tick = QtCore.QTimer(self)
        self.tick.timeout.connect(self.update_loop)
        self.tick.start(16)

        # 시작 위치
        scr = available_geo(self)
        start_x = scr.x() + max(40, scr.width() // 2 - self.width() // 2)
        self.move(start_x, scr.y() + 40)
        self._snap_floor()

        # 드래그 기록
        self.drag_trace = deque(maxlen=6)

    # -------------------------------------------------
    # 사전 디코딩
    # -------------------------------------------------
    def _predecode_all(self):
        base = BASE_DIR / "assets" / CHAR_NAME
        for action, rel_path in ACTIONS.items():
            gif_path = base / rel_path
            if gif_path.exists():
                frames, delays, max_w, max_h = self._decode_gif(str(gif_path))
            else:
                png_dir = gif_path.parent
                frames, delays, max_w, max_h = self._decode_png_folder(png_dir)
            self.raw_animations[action] = list(zip(frames, delays))
            self.anim_max_size[action] = (max_w, max_h)

    def _rebuild_scaled_cache(self):
        self.animations = {}
        for action, raw_list in self.raw_animations.items():
            scaled_list = []
            for (pm, delay) in raw_list:
                if pm.isNull():
                    spm = QtGui.QPixmap(32, 32)
                    spm.fill(QtCore.Qt.transparent)
                    scaled_list.append((spm, delay))
                    continue
                w = pm.width()
                h = pm.height()
                sw = max(1, int(w * self.scale))
                sh = max(1, int(h * self.scale))
                spm = pm.scaled(sw, sh, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                scaled_list.append((spm, delay))
            self.animations[action] = scaled_list

    def _decode_gif(self, path):
        movie = QtGui.QMovie(path)
        frames = []
        delays = []
        max_w = 1
        max_h = 1
        idx = 0
        while True:
            if not movie.jumpToFrame(idx):
                break
            pix = movie.currentPixmap()
            if pix.isNull():
                break
            w = pix.width()
            h = pix.height()
            max_w = max(max_w, w)
            max_h = max(max_h, h)
            frames.append(pix)
            d = movie.nextFrameDelay()
            if d <= 0:
                d = MIN_FRAME_DELAY
            delays.append(d / 1000.0)
            idx += 1
        if not frames:
            pm = QtGui.QPixmap(64, 64)
            pm.fill(QtCore.Qt.transparent)
            return [pm], [0.2], 64, 64
        return frames, delays, max_w, max_h

    def _decode_png_folder(self, folder: Path):
        if not folder.exists():
            pm = QtGui.QPixmap(64, 64)
            pm.fill(QtCore.Qt.transparent)
            return [pm], [0.2], 64, 64
        files = sorted(
            [p for p in folder.iterdir() if p.suffix.lower() in (".png", ".webp", ".jpg", ".jpeg")],
            key=lambda p: p.name
        )
        if not files:
            pm = QtGui.QPixmap(64, 64)
            pm.fill(QtCore.Qt.transparent)
            return [pm], [0.2], 64, 64
        frames = []
        delays = []
        max_w = 1
        max_h = 1
        for p in files:
            pm = QtGui.QPixmap(p.as_posix())
            if pm.isNull():
                continue
            w = pm.width()
            h = pm.height()
            max_w = max(max_w, w)
            max_h = max(max_h, h)
            frames.append(pm)
            delays.append(0.12)
        return frames, delays, max_w, max_h

    # -------------------------------------------------
    # 메뉴
    # -------------------------------------------------
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
        self.size_menu = self.menu.addMenu("크기")
        self.size_actions = []
        for name, scale in SCALE_PRESETS:
            act = self.size_menu.addAction(name)
            act.setCheckable(True)
            act._scale_value = scale
            self.size_actions.append(act)
        for act in self.size_actions:
            if abs(act._scale_value - self.scale) < 1e-6:
                act.setChecked(True)

        self.menu.addSeparator()
        self.act_spawn  = self.menu.addAction("펫 추가")
        self.act_close  = self.menu.addAction("이 펫 닫기")

        for a in [self.act_follow, self.act_random, self.act_dance, self.act_ex, self.act_sleep]:
            a.setCheckable(True)

    def _refresh_checks(self):
        self.act_follow.setChecked(self.follow_mouse)
        self.act_random.setChecked(self.random_walk)
        self.act_dance.setChecked(self.mode == "dance")
        self.act_ex.setChecked(self.mode == "exercise")
        self.act_sleep.setChecked(self.mode == "sleep")

    def contextMenuEvent(self, ev):
        self.menu_open = True
        action = self.menu.exec_(self.mapToGlobal(ev.pos()))
        self.menu_open = False

        if action in self.size_actions:
            self.scale = action._scale_value
            for act in self.size_actions:
                act.setChecked(act is action)
            self._rebuild_scaled_cache()
            if self.current_action:
                self.current_frame_idx = 0
                self._apply_current_frame()
            self._snap_floor()
            return

        if action == self.act_follow:
            self.follow_mouse = not self.follow_mouse
            if self.follow_mouse:
                self.random_walk = False
                self.stop_move = False

        elif action == self.act_random:
            self.random_walk = not self.random_walk
            if self.random_walk:
                self.follow_mouse = False
                self.stop_move = False
                self.vx = random.choice([-2.0, 2.0])
                self.set_action("walk_right" if self.vx > 0 else "walk_left", force=True)
            else:
                self.vx = 0.0

        elif action == self.act_dance:
            if self.mode == "dance":
                self.mode = "normal"; self.stop_move = False
                self.set_action("idle", force=True)
            else:
                self._exit_modes()
                self.mode = "dance"; self.stop_move = True
                self.set_action("dance", force=True)

        elif action == self.act_ex:
            if self.mode == "exercise":
                self._exit_modes(); self.set_action("idle", force=True)
            else:
                self._exit_modes()
                self.mode = "exercise"; self.stop_move = True
                first = random.choice(self.exercise_cycle)
                self.set_action(first, force=True)
                self.exercise_idx = self.exercise_cycle.index(first)
                self.exercise_timer.start(10_000)

        elif action == self.act_sleep:
            if self.mode == "sleep":
                self._exit_modes(); self.set_action("idle", force=True)
            else:
                self._exit_modes()
                self.mode = "sleep"; self.stop_move = True
                self.set_action("sleep", force=True)

        elif action == self.act_eat:
            # 토글 중이면 먹이도 안 줌
            if self.mode not in ("dance","sleep","exercise"):
                self.play_temp("eat", 6000, stop_during_temp=True)

        elif action == self.act_pet:
            if self.mode not in ("dance","sleep","exercise"):
                self.play_temp("pet", 6000, stop_during_temp=True)

        elif action == self.act_spawn:
            g = self.geometry()
            self.mgr.spawn(pos=QtCore.QPoint(g.x()+40, g.y()+20))

        elif action == self.act_close:
            self.mgr.remove(self)

        self._refresh_checks()

    def _exit_modes(self):
        if self.mode == "exercise":
            self.exercise_timer.stop()
        self.mode = "normal"
        self.stop_move = False

    # -------------------------------------------------
    # 운동 모드
    # -------------------------------------------------
    def _exercise_next(self):
        if self.mode != "exercise":
            self.exercise_timer.stop()
            return
        self.exercise_idx = (self.exercise_idx + 1) % len(self.exercise_cycle)
        self.set_action(self.exercise_cycle[self.exercise_idx], force=True)

    # -------------------------------------------------
    # 입력
    # -------------------------------------------------
    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            self.raise_()
            self.activateWindow()
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
                # 토글이면 모션 바꾸지 않고 위치만 이동
                if self.mode not in ("dance","sleep","exercise"):
                    self.set_action("hang", force=True)
        if self.dragging:
            self._record_drag_point(ev.globalPos())
            self.move(ev.globalPos() - self.drag_offset)
            self._clamp_to_screen()
            # 토글 중에는 climb 진입도 금지
            if self.mode in ("dance","sleep","exercise"):
                return
            g = self.geometry(); scr = available_geo(self)
            if g.x() <= scr.x() + EDGE_MARGIN:
                self._enter_climb("left")
            elif g.x() >= scr.x() + scr.width() - self.current_pix_w - EDGE_MARGIN:
                self._enter_climb("right")

    def mouseReleaseEvent(self, ev):
        if ev.button() != QtCore.Qt.LeftButton:
            return
        if not self.dragging:
            # 클릭 처리
            if self.single_click_timer.isActive():
                self.single_click_timer.stop()
                # 토글이면 surprise도 안 나옴
                if self.mode not in ("dance","sleep","exercise"):
                    self._trigger_single_click()
            self.press_pos = None
            return

        # 드래그였다 → 던지기
        self.dragging = False
        self._apply_throw_velocity()

        # 탄성 크게
        scr = available_geo(self)
        bottom_y = scr.y() + scr.height() - self.current_pix_h - FLOOR_MARGIN
        if abs(self.y() - bottom_y) <= 3:
            self.vy = -14.0
            self.move(self.x(), bottom_y)
        else:
            self.vy = max(self.vy, 9.0)

        if self.mode in ("dance","sleep","exercise"):
            self.press_pos = None
            return

        g = self.geometry()
        if g.x() <= scr.x() + EDGE_MARGIN:
            self._enter_climb("left"); self.press_pos = None; return
        if g.x() >= scr.x() + scr.width() - self.current_pix_w - EDGE_MARGIN:
            self._enter_climb("right"); self.press_pos = None; return

        if self.random_walk:
            self.vx = 2.0 if self.vx >= 0 else -2.0

        if self.current_action != "hang":
            self.set_action("hang", force=True)

        self.press_pos = None

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            if self.single_click_timer.isActive():
                self.single_click_timer.stop()
            # 토글 중이면 angry도 안 나옴
            if self.mode not in ("dance","sleep","exercise"):
                self.play_temp("angry", 6000)

    def _trigger_single_click(self):
        # 토글 상태에서는 호출되지 않게 위에서 막아놨음
        self.play_temp("surprise", 6000)

    # -------------------------------------------------
    # 드래그 속도
    # -------------------------------------------------
    def _record_drag_point(self, gpos: QtCore.QPoint):
        self.drag_trace.append((QtCore.QPoint(gpos), time.monotonic()))

    def _apply_throw_velocity(self):
        if len(self.drag_trace) < 2:
            return
        (p2, t2) = self.drag_trace[-1]; (p1, t1) = self.drag_trace[0]
        dt = max(1e-3, (t2 - t1))
        dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
        frames = dt / 0.016
        self.vx = dx / max(1.0, frames)
        self.vy = dy / max(1.0, frames)

        speed = ((dx ** 2 + dy ** 2) ** 0.5) / dt
        if speed > THROW_ANGRY_SPEED and self.mode not in ("dance","sleep","exercise"):
            self.play_temp("angry", 6000)

    # -------------------------------------------------
    # 임시 모션
    # -------------------------------------------------
    def play_temp(self, key, hold_ms, on_done=None, stop_during_temp=False):
        # 토글 상태에서는 surprise/angry/eat/pet 전부 막음
        if self.mode in ("dance","sleep","exercise"):
            return
        self.temp_token += 1
        token = self.temp_token
        self.active_temp_action = key
        self.set_action(key, force=True)
        if stop_during_temp:
            self.stop_move = True
        self.force_action_until = time.monotonic() + (hold_ms / 1000.0)

        def _end():
            if on_done: on_done()
            self._end_temp(token)
        QtCore.QTimer.singleShot(hold_ms, _end)

    def _end_temp(self, token):
        if token != self.temp_token:
            return
        self.force_action_until = 0.0
        self.active_temp_action = None
        if self.mode in ("dance","exercise","sleep"):
            return
        if self.follow_mouse or self.random_walk:
            return
        self.stop_move = False
        self.set_action("idle", force=True)

    # -------------------------------------------------
    # 액션
    # -------------------------------------------------
    def set_action(self, key, force=False):
        # 토글 상태면 어떤 액션도 못 들어오게 (완전 잠금)
        if self.mode in ("dance","sleep","exercise") and not force:
            return

        # temp 중인데 다른 모션 들어오면 끊기
        if self.active_temp_action and key != self.active_temp_action:
            self.active_temp_action = None
            self.force_action_until = 0.0

        if not force and key == self.current_action:
            return
        if key not in self.animations:
            return

        self.current_action = key
        self.current_frame_idx = 0
        now = time.monotonic()
        frames = self.animations[key]
        if frames:
            self.next_frame_time = now + frames[0][1]
            self._apply_frame(frames[0][0], key)
        else:
            self.next_frame_time = now + 0.2

    def _apply_frame(self, pix: QtGui.QPixmap, action_key: str = None):
        self.label.setPixmap(pix)
        dpr = pix.devicePixelRatio() or 1.0
        self.current_pix_w = int(pix.width() / dpr)
        self.current_pix_h = int(pix.height() / dpr)

        self.label.resize(self.current_pix_w, self.current_pix_h)
        self.setFixedSize(self.current_pix_w + WINDOW_PAD, self.current_pix_h + WINDOW_PAD)

        if BG_MODE == "chroma":
            self.setMask(pix.createMaskFromColor(QtGui.QColor(255,255,255), QtCore.Qt.MaskOutColor))
        else:
            self.clearMask()

        if action_key is None:
            action_key = self.current_action
        if action_key not in FLOOR_SNAP_EXCLUDE:
            self._snap_floor()

    def _apply_current_frame(self):
        if not self.current_action:
            return
        frames = self.animations.get(self.current_action)
        if not frames:
            return
        idx = min(self.current_frame_idx, len(frames)-1)
        pix, _ = frames[idx]
        self._apply_frame(pix, self.current_action)

    # -------------------------------------------------
    # 애니 업데이트
    # -------------------------------------------------
    def _update_animation(self, now: float):
        if not self.current_action:
            return
        frames = self.animations.get(self.current_action)
        if not frames:
            return
        if now < self.next_frame_time:
            return
        self.current_frame_idx = (self.current_frame_idx + 1) % len(frames)
        pix, delay = frames[self.current_frame_idx]
        self._apply_frame(pix, self.current_action)
        self.next_frame_time = now + delay
        if self._sync_frames_left > 0:
            self._sync_frames_left -= 1

    # -------------------------------------------------
    # 유틸
    # -------------------------------------------------
    def _clamp_to_screen(self):
        g = self.geometry(); scr = available_geo(self)
        x = max(scr.x(), min(g.x(), scr.x()+scr.width()-self.width()))
        y = max(scr.y(), min(g.y(), scr.y()+scr.height()-self.height()))
        if x != g.x() or y != g.y():
            self.move(x, y)

    def _snap_floor(self):
        scr = available_geo(self)
        bottom_y = scr.y() + scr.height() - self.current_pix_h - FLOOR_MARGIN
        x = self.x()
        if x == 0:
            x = scr.x() + 40
        self.move(x, bottom_y)

    def _enter_climb(self, side: str):
        # 토글 상태에서는 climb도 무효
        if self.mode in ("dance","sleep","exercise"):
            return

        scr = available_geo(self)
        if side == "left":
            x = scr.x()
            y = min(self.y(), scr.y() + scr.height() - self.current_pix_h - FLOOR_MARGIN)
            self.move(x, y)
            self.set_action("climb_left", force=True)
            self.follow_resume_dir = 1
        else:
            x = scr.x() + scr.width() - self.current_pix_w
            y = min(self.y(), scr.y() + scr.height() - self.current_pix_h - FLOOR_MARGIN)
            self.move(x, y)
            self.set_action("climb_right", force=True)
            self.follow_resume_dir = -1

        self.climb_hold_until = time.monotonic() + 10.0
        if self.climb_hold_timer:
            try: self.climb_hold_timer.stop()
            except Exception: pass
        self.climb_hold_timer = QtCore.QTimer(self)
        self.climb_hold_timer.setSingleShot(True)
        self.climb_hold_timer.timeout.connect(self._end_climb_hold)
        self.climb_hold_timer.start(10_000)

    def _end_climb_hold(self):
        if self.current_action in ("climb_left","climb_right"):
            self.set_action("hang", force=True)
            self.vy = max(self.vy, 2.0)
            self.follow_resume_deadline = time.monotonic() + 1.5
            self.force_run_until = time.monotonic() + 0.8
            if self.random_walk:
                self.vx = 2.0 if self.follow_resume_dir >= 0 else -2.0
                self.set_action("walk_right" if self.vx > 0 else "walk_left", force=True)

    # -------------------------------------------------
    # 메인 루프
    # -------------------------------------------------
    def update_loop(self):
        now = time.monotonic()

        # 드래그가 남아있는데 버튼이 올라갔으면 강제로 끄기
        if self.dragging and not QtWidgets.QApplication.mouseButtons() & QtCore.Qt.LeftButton:
            self.dragging = False

        # 프레임 먼저
        self._update_animation(now)

        if self.menu_open:
            return

        # 토글 모드면 멈춰
        if self.mode in ("dance","exercise","sleep"):
            return

        g = self.geometry()
        scr = available_geo(self)
        left_edge = scr.x()
        right_edge = scr.x() + scr.width() - self.current_pix_w
        bottom = scr.y() + scr.height() - self.current_pix_h

        in_climb = self.current_action in ("climb_left","climb_right")

        # 1) 중력
        if not self.stop_move and not self.dragging:
            if g.y() < bottom and not in_climb:
                self.vy += GRAVITY
                ny = min(bottom, g.y() + int(self.vy))
                self.move(g.x(), ny)
                if ny >= bottom:
                    if abs(self.vy) > 3.5:
                        self.vy = -abs(self.vy) * BOUNCE_K
                        self.vx *= 0.9
                    else:
                        self.vy = 0.0
                        if not (self.follow_mouse or self.random_walk):
                            self.set_action("idle")
                return
            else:
                self.vy = 0.0

        if self.stop_move or self.dragging:
            return

        # 2) climb 유지
        if in_climb:
            if self.climb_hold_until and now >= self.climb_hold_until:
                self._end_climb_hold()
            return

        # 3) 마우스 따라가기
        if self.follow_mouse:
            mp = QtGui.QCursor.pos()
            cx = g.x() + self.current_pix_w // 2
            dist = abs(mp.x() - cx)

            # 커서 움직임 판단
            dx_cur = abs(mp.x() - self.last_cursor_pos.x())
            dy_cur = abs(mp.y() - self.last_cursor_pos.y())
            cursor_still = (dx_cur < CURSOR_STILL_EPS and dy_cur < CURSOR_STILL_EPS)

            # 3-1) 커서가 가깝고, 커서가 안 움직이는 경우 → 계속 jump
            if dist <= FOLLOW_JUMP_NEAR and cursor_still:
                if now >= self.jump_hold_until:
                    # 쿨다운 무시하고 계속 점프
                    self.set_action("jump", force=True)
                    self.jump_hold_until = now + FOLLOW_JUMP_HOLD
                # 커서 기록 갱신 후 return
                self.last_cursor_pos = mp
                self.last_cursor_t = now
                return

            # 3-2) 커서가 가깝지만 움직인 경우 → 원래 쿨다운 방식
            if dist <= FOLLOW_JUMP_NEAR and now >= self.jump_cooldown_until:
                self.set_action("jump", force=True)
                self.jump_cooldown_until = now + FOLLOW_JUMP_COOLDOWN
                self.jump_hold_until = now + FOLLOW_JUMP_HOLD
                self.last_cursor_pos = mp
                self.last_cursor_t = now
                return

            # 3-3) 커서가 멀면 원래 쫓아감
            if g.y() >= bottom:
                dx = mp.x() - cx
                speed = 6 if (now < self.force_run_until or dist > FOLLOW_FAST_DIST) else 3
                step = speed if dx > 0 else -speed
                nx = g.x() + step
                nx = max(left_edge, min(right_edge, nx))
                self.move(nx, g.y())
                if dist > FOLLOW_RUN_DIST:
                    want = "run_right" if dx > 0 else "run_left"
                else:
                    want = "walk_right" if dx > 0 else "walk_left"
                if want != self.current_action:
                    self.set_action(want)
            self.last_cursor_pos = mp
            self.last_cursor_t = now
            return

        # 4) 랜덤 이동
        if self.random_walk:
            if abs(self.vx) > 3.0:
                self.vx = 3.0 if self.vx > 0 else -3.0
            if self.vx == 0:
                self.vx = random.choice([-2.0, 2.0])
            nx = g.x() + int(self.vx)
            if nx <= left_edge:
                nx = left_edge; self.vx = abs(self.vx)
            elif nx >= right_edge:
                nx = right_edge; self.vx = -abs(self.vx)
            self.move(nx, g.y())
            want = "walk_right" if self.vx > 0 else "walk_left"
            if want != self.current_action:
                self.set_action(want)
            return

        # 5) 기본
        if self.current_action != "idle":
            self.set_action("idle")


# =====================================================
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
