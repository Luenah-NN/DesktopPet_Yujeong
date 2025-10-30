# -*- coding: utf-8 -*-
import sys, os, random, time
from collections import deque
from pathlib import Path
from PyQt5 import QtCore, QtGui, QtWidgets

# ==================== 전역 설정 ====================
CHAR_NAME = "Yujeong"
BG_MODE   = "rembg"     # "chroma" or "rembg"
BASE_DIR  = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))

# 모든 모션 12fps로
DISPLAY_FPS   = 12
DISPLAY_DELAY = 1.0 / DISPLAY_FPS

MIN_FRAME_DELAY      = 40
INITIAL_SYNC_FRAMES  = 2
WINDOW_PAD           = 2
EDGE_MARGIN          = 10
FLOOR_MARGIN         = 2

GRAVITY              = 1.1

# 바운스
BOUNCE_K             = 0.78
BOUNCE_MAX           = 2
BOUNCE_MIN_VEL       = 3.5
BOUNCE_UP_VEL_FLOOR  = 11.0
BOUNCE_CLEAR_EPS     = 2

THROW_ANGRY_SPEED    = 1200.0

# 마우스 따라가기 점프
FOLLOW_JUMP_NEAR     = 60
FOLLOW_JUMP_HOLD     = 0.6
FOLLOW_JUMP_COOLDOWN = 0.8  # (남겨둠)
FOLLOW_RUN_DIST      = 200
FOLLOW_FAST_DIST     = 400
CURSOR_STILL_EPS     = 3

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

# 바닥에 안 붙이는 애들
FLOOR_SNAP_EXCLUDE = {"climb_left", "climb_right", "hang"}


def available_geo(window: QtWidgets.QWidget) -> QtCore.QRect:
    win = window.windowHandle()
    if win and win.screen():
        return win.screen().availableGeometry()
    scr = QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos())
    return scr.availableGeometry() if scr else QtWidgets.QApplication.primaryScreen().availableGeometry()


# ==================== Manager ====================
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


# ==================== Pet ====================
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
        self.setCentralWidget(self.label)

        # 현재 프레임 크기
        self.current_pix_w = 64
        self.current_pix_h = 64
        self.current_floor_h = 64

        # 스케일
        self.scale = 0.65

        # 사전 디코딩
        self.raw_animations = {}
        self.animations     = {}
        self.anim_max_size  = {}
        self.scaled_max_size = {}
        self.anim_meta      = {}

        self._predecode_all()
        self._rebuild_scaled_cache()

        # 물리/상태
        self.vx, self.vy = 0.0, 0.0
        self.dragging = False
        self.drag_offset = QtCore.QPoint(0, 0)
        self.press_pos = None
        self.drag_threshold = 6

        self.follow_mouse = False
        self.random_walk  = False
        self.stop_move    = False
        self.mode         = "normal"   # dance/sleep/exercise면 다른 모션 막기
        self.menu_open    = False

        # 점프 상태
        self.jump_cooldown_until = 0.0
        self.jump_hold_until     = 0.0
        self.follow_force_jump_until = 0.0  # ← 가까워졌을 때 강제 jump 유지용

        # 커서 기록
        self.last_cursor_pos = QtCore.QPoint(0, 0)
        self.last_cursor_t   = 0.0

        # 운동 모드
        self.exercise_cycle = ["squat","boxing","plank","jumping_jacks"]
        self.exercise_idx   = 0
        self.exercise_timer = QtCore.QTimer(self)
        self.exercise_timer.timeout.connect(self._exercise_next)

        # 임시 모션
        self.force_action_until = 0.0
        self.temp_token = 0
        self.active_temp_action = None   # ← eat/pet 같은 거 들어있음

        # 클릭
        self.single_click_timer = QtCore.QTimer(self)
        self.single_click_timer.setSingleShot(True)
        self.single_click_timer.timeout.connect(self._trigger_single_click)

        # climb
        self.climb_hold_until = 0.0
        self.climb_hold_timer = None
        self.follow_resume_dir = 0
        self.follow_resume_deadline = 0.0
        self.force_run_until = 0.0

        # 바운스 상태
        self.bounce_count  = 0
        self.is_bouncing   = False

        # 메뉴
        self._make_menu()

        # 애니
        self.current_action    = None
        self.current_frame_idx = 0
        self.next_frame_time   = time.monotonic()
        self._sync_frames_left = INITIAL_SYNC_FRAMES

        # 초기 모션
        self.set_action("idle", force=True)

        # 메인 루프
        self.tick = QtCore.QTimer(self)
        self.tick.timeout.connect(self.update_loop)
        self.tick.start(16)

        # 시작 위치
        scr = available_geo(self)
        start_x = scr.x() + max(40, scr.width() // 2 - self.width() // 2)
        self.move(start_x, scr.y() + 40)
        self._snap_floor()

        # 던지기 기록
        self.drag_trace = deque(maxlen=6)

    # ==================== 디코딩 & 스케일 ====================
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

            if delays:
                avg_delay = sum(delays) / len(delays)
            else:
                avg_delay = 0.05
            if avg_delay <= 0:
                avg_delay = 0.05
            orig_fps = 1.0 / avg_delay
            self.anim_meta[action] = {"avg_delay": avg_delay, "orig_fps": orig_fps}

    def _rebuild_scaled_cache(self):
        self.animations = {}
        self.scaled_max_size = {}
        for action, raw_list in self.raw_animations.items():
            scaled_list = []
            max_w_raw, max_h_raw = self.anim_max_size.get(action, (64, 64))
            max_w_s = max(1, int(max_w_raw * self.scale))
            max_h_s = max(1, int(max_h_raw * self.scale))
            for (pm, delay) in raw_list:
                if pm.isNull():
                    spm = QtGui.QPixmap(32, 32); spm.fill(QtCore.Qt.transparent)
                    scaled_list.append((spm, delay))
                else:
                    sw = max(1, int(pm.width() * self.scale))
                    sh = max(1, int(pm.height() * self.scale))
                    spm = pm.scaled(sw, sh, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                    scaled_list.append((spm, delay))
            self.animations[action] = scaled_list
            self.scaled_max_size[action] = (max_w_s, max_h_s)

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
            pm = QtGui.QPixmap(64, 64); pm.fill(QtCore.Qt.transparent)
            return [pm], [0.05], 64, 64
        return frames, delays, max_w, max_h

    def _decode_png_folder(self, folder: Path):
        if not folder.exists():
            pm = QtGui.QPixmap(64, 64); pm.fill(QtCore.Qt.transparent)
            return [pm], [0.05], 64, 64
        files = sorted([p for p in folder.iterdir() if p.suffix.lower() in (".png",".webp",".jpg",".jpeg")],
                       key=lambda p: p.name)
        if not files:
            pm = QtGui.QPixmap(64, 64); pm.fill(QtCore.Qt.transparent)
            return [pm], [0.05], 64, 64
        frames, delays = [], []
        max_w = 1; max_h = 1
        for p in files:
            pm = QtGui.QPixmap(p.as_posix())
            if pm.isNull():
                continue
            w = pm.width(); h = pm.height()
            max_w = max(max_w, w); max_h = max(max_h, h)
            frames.append(pm)
            delays.append(0.05)
        return frames, delays, max_w, max_h

    # ==================== 메뉴 ====================
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
        for act in self.size_actions:
            if abs(act._scale_value - self.scale) < 1e-6:
                act.setChecked(True)
        self.menu.addSeparator()
        self.act_spawn = self.menu.addAction("펫 추가")
        self.act_close = self.menu.addAction("이 펫 닫기")

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

        # 크기 변경
        if action in self.size_actions:
            self.scale = action._scale_value
            for a in self.size_actions:
                a.setChecked(a is action)
            self._rebuild_scaled_cache()
            if self.current_action:
                _, h = self.scaled_max_size.get(self.current_action, (self.current_pix_w, self.current_pix_h))
                self.current_floor_h = h
                self._apply_current_frame()
                if self.current_action not in FLOOR_SNAP_EXCLUDE:
                    self._snap_floor()
            return

        # 마우스 따라가기
        if action == self.act_follow:
            # ✅ 토글 켜져있으면 우선 끄고 follow
            if self.mode in ("dance","exercise","sleep"):
                self._exit_modes()
            self.follow_mouse = not self.follow_mouse
            if self.follow_mouse:
                self.random_walk = False
                self.stop_move = False

        # 랜덤 이동
        elif action == self.act_random:
            # ✅ 토글 켜져있으면 우선 끄고 random
            if self.mode in ("dance","exercise","sleep"):
                self._exit_modes()
            self.random_walk = not self.random_walk
            if self.random_walk:
                self.follow_mouse = False
                self.stop_move = False
                self.vx = random.choice([-2.0, 2.0])
                self.set_action("walk_right" if self.vx>0 else "walk_left", force=True)
            else:
                self.vx = 0.0

        # 춤추기
        elif action == self.act_dance:
            if self.mode == "dance":
                self._exit_modes(); self.set_action("idle", force=True); self._snap_floor()
            else:
                self._exit_modes()
                self.mode = "dance"; self.stop_move = True
                self.set_action("dance", force=True); self._snap_floor()

        # 운동하기
        elif action == self.act_ex:
            if self.mode == "exercise":
                self._exit_modes(); self.set_action("idle", force=True); self._snap_floor()
            else:
                self._exit_modes()
                self.mode = "exercise"; self.stop_move = True
                first = random.choice(self.exercise_cycle)
                self.set_action(first, force=True)
                self.exercise_idx = self.exercise_cycle.index(first)
                self.exercise_timer.start(10_000)
                self._snap_floor()

        # 잠자기
        elif action == self.act_sleep:
            if self.mode == "sleep":
                self._exit_modes(); self.set_action("idle", force=True); self._snap_floor()
            else:
                self._exit_modes()
                self.mode = "sleep"; self.stop_move = True
                self.set_action("sleep", force=True); self._snap_floor()

        # 간식주기
        elif action == self.act_eat:
            # ✅ 토글 켜져있으면 끄고 실행
            if self.mode in ("dance","sleep","exercise"):
                self._exit_modes()
            self.play_temp("eat", 6000, stop_during_temp=True)

        # 쓰다듬기
        elif action == self.act_pet:
            # ✅ 토글 켜져있으면 끄고 실행
            if self.mode in ("dance","sleep","exercise"):
                self._exit_modes()
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

    # ==================== 운동 모드 ====================
    def _exercise_next(self):
        if self.mode != "exercise":
            self.exercise_timer.stop(); return
        self.exercise_idx = (self.exercise_idx + 1) % len(self.exercise_cycle)
        self.set_action(self.exercise_cycle[self.exercise_idx], force=True)
        self._snap_floor()

    # ==================== 입력 ====================
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

                # ✅ 간식/쓰다듬기 중에도 드래그하면 바로 hang으로! (temp 해제)
                if self.active_temp_action in ("eat", "pet"):
                    self.active_temp_action = None
                    self.force_action_until = 0.0
                    self.stop_move = False
                    self.set_action("hang", force=True)
                else:
                    if self.mode not in ("dance","sleep","exercise"):
                        self.set_action("hang", force=True)

        if self.dragging:
            self._record_drag_point(ev.globalPos())
            self.move(ev.globalPos() - self.drag_offset)
            self._clamp_to_screen()
            self.bounce_count = 0
            self.is_bouncing = False

            # 토글 모드일 때는 여기까지 (끌기는 되되, 밑에 벽 감지는 안 해도 됨)
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
            if self.single_click_timer.isActive():
                self.single_click_timer.stop()
                if self.mode not in ("dance","sleep","exercise"):
                    self._trigger_single_click()
            self.press_pos = None
            return

        self.dragging = False
        self._apply_throw_velocity()
        self.bounce_count = 0
        self.is_bouncing  = False

        scr = available_geo(self)
        bottom_y = scr.y() + scr.height() - self.current_floor_h - FLOOR_MARGIN
        if abs(self.y() - bottom_y) <= 3:
            self.vy = -13.0
        else:
            self.vy = max(self.vy, 9.0)

        # 토글 모드면 여기서 끝
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
            if self.mode not in ("dance","sleep","exercise"):
                self.play_temp("angry", 6000)

    def _trigger_single_click(self):
        if self.mode in ("dance","sleep","exercise"):
            return
        self.play_temp("surprise", 6000)

    # ==================== 드래그 속도 ====================
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

    # ==================== 임시 모션 ====================
    def play_temp(self, key, hold_ms, on_done=None, stop_during_temp=False):
        # 여기까지 오기 전에 contextMenu에서 토글을 끄게 해놨으므로
        # 여기서는 모드 검사 그대로 둔다
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
        self._snap_floor()

    # ==================== 액션 ====================
    def set_action(self, key, force=False):
        if self.mode in ("dance","sleep","exercise") and not force:
            return
        if self.active_temp_action and key != self.active_temp_action:
            self.active_temp_action = None
            self.force_action_until = 0.0
            self.stop_move = False
        if not force and key == self.current_action:
            return
        if key not in self.animations:
            return

        self.current_action = key
        self.current_frame_idx = 0
        now = time.monotonic()
        frames = self.animations[key]
        _, h = self.scaled_max_size.get(key, (self.current_pix_w, self.current_pix_h))
        self.current_floor_h = h

        if frames:
            self.next_frame_time = now + DISPLAY_DELAY
            self._apply_frame(frames[0][0])
        else:
            self.next_frame_time = now + DISPLAY_DELAY

        if key not in FLOOR_SNAP_EXCLUDE and not self.is_bouncing:
            self._snap_floor()

    def _apply_frame(self, pix: QtGui.QPixmap):
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

    def _apply_current_frame(self):
        if not self.current_action:
            return
        frames = self.animations.get(self.current_action)
        if not frames:
            return
        idx = min(self.current_frame_idx, len(frames) - 1)
        self._apply_frame(frames[idx][0])

    # ==================== 애니 업데이트 (12fps) ====================
    def _update_animation(self, now: float):
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
        if self._sync_frames_left > 0:
            self._sync_frames_left -= 1

    # ==================== 바닥/클램프 ====================
    def _floor_y(self):
        scr = available_geo(self)
        return scr.y() + scr.height() - self.current_floor_h - FLOOR_MARGIN

    def _snap_floor(self):
        if self.is_bouncing:
            return
        fy = self._floor_y()
        self.move(self.x(), fy)

    def _ensure_on_floor(self):
        if self.is_bouncing:
            return
        if self.current_action in FLOOR_SNAP_EXCLUDE:
            return
        fy = self._floor_y()
        if abs(self.y() - fy) > 1:
            self.move(self.x(), fy)

    def _clamp_to_screen(self):
        g = self.geometry(); scr = available_geo(self)
        x = max(scr.x(), min(g.x(), scr.x()+scr.width()-self.width()))
        y = max(scr.y(), min(g.y(), scr.y()+scr.height()-self.height()))
        if x != g.x() or y != g.y():
            self.move(x, y)

    def _enter_climb(self, side: str):
        if self.mode in ("dance","sleep","exercise"):
            return
        scr = available_geo(self)
        if side == "left":
            x = scr.x()
        else:
            x = scr.x() + scr.width() - self.current_pix_w
        y = min(self.y(), self._floor_y())
        self.move(x, y)
        self.set_action("climb_left" if side=="left" else "climb_right", force=True)
        self.climb_hold_until = time.monotonic() + 10.0
        if self.climb_hold_timer:
            try: self.climb_hold_timer.stop()
            except Exception: pass
        self.climb_hold_timer = QtCore.QTimer(self)
        self.climb_hold_timer.setSingleShot(True)
        self.climb_hold_timer.timeout.connect(self._end_climb_hold)
        self.climb_hold_timer.start(10_000)
        self.bounce_count = 0
        self.is_bouncing  = False

    def _end_climb_hold(self):
        if self.current_action in ("climb_left","climb_right"):
            self.set_action("hang", force=True)
            self.vy = max(self.vy, 2.0)
            self.follow_resume_deadline = time.monotonic() + 1.5
            self.force_run_until = time.monotonic() + 0.8

    # ==================== 메인 루프 ====================
    def update_loop(self):
        now = time.monotonic()
        self._update_animation(now)

        if self.menu_open:
            return

        g = self.geometry()
        scr = available_geo(self)
        left_edge  = scr.x()
        right_edge = scr.x() + scr.width() - self.current_pix_w
        bottom     = self._floor_y()
        in_climb   = self.current_action in ("climb_left","climb_right")

        # 1) 튕기는 중
        if self.is_bouncing:
            self.vy += GRAVITY
            ny = g.y() + int(self.vy)
            if ny >= bottom:
                self.bounce_count += 1
                if (self.bounce_count < BOUNCE_MAX) and (abs(self.vy) > BOUNCE_MIN_VEL):
                    up_vel = max(abs(self.vy) * BOUNCE_K, BOUNCE_UP_VEL_FLOOR)
                    self.vy = -up_vel
                    self.move(g.x(), bottom - BOUNCE_CLEAR_EPS)
                    return
                else:
                    self.is_bouncing = False
                    self.bounce_count = 0
                    self.vy = 0.0
                    self.move(g.x(), bottom)
                    if not (self.follow_mouse or self.random_walk):
                        self.set_action("idle", force=True)
                    return
            else:
                self.move(g.x(), ny)
                return

        # 2) 토글 모드면 여기서 끝
        if self.mode in ("dance","exercise","sleep"):
            return

        # 3) 일반 중력
        if not self.stop_move and not self.dragging:
            if g.y() < bottom and not in_climb:
                self.vy += GRAVITY
                ny = g.y() + int(self.vy)
                if ny >= bottom:
                    if abs(self.vy) > BOUNCE_MIN_VEL:
                        up_vel = max(abs(self.vy) * BOUNCE_K, BOUNCE_UP_VEL_FLOOR)
                        self.vy = -up_vel
                        self.is_bouncing = True
                        self.bounce_count = 1
                        self.move(g.x(), bottom - BOUNCE_CLEAR_EPS)
                        return
                    else:
                        self.vy = 0.0
                        self.move(g.x(), bottom)
                        if not (self.follow_mouse or self.random_walk):
                            self.set_action("idle", force=True)
                        return
                else:
                    self.move(g.x(), ny)
                    return
            else:
                self.vy = 0.0

        if self.stop_move or self.dragging:
            return

        # 4) climb 유지
        if in_climb:
            if self.climb_hold_until and now >= self.climb_hold_until:
                self._end_climb_hold()
            return

        # 5) 마우스 따라가기 (버벅임 제거 버전)
        if self.follow_mouse:
            mp = QtGui.QCursor.pos()
            cx = g.x() + self.current_pix_w // 2
            dist = abs(mp.x() - cx)

            # jump zone
            if dist <= FOLLOW_JUMP_NEAR:
                if now >= self.follow_force_jump_until:
                    self.follow_force_jump_until = now + FOLLOW_JUMP_HOLD
                if self.current_action != "jump":
                    self.set_action("jump", force=True)
                self.vx = 0.0
                self.move(g.x(), min(g.y(), bottom))
                self.last_cursor_pos = mp
                self.last_cursor_t = now
                return

            if now < self.follow_force_jump_until:
                if self.current_action != "jump":
                    self.set_action("jump", force=True)
                self.last_cursor_pos = mp
                self.last_cursor_t = now
                return

            # 평소 run/walk
            if g.y() >= bottom:
                dx = mp.x() - cx
                speed = 6 if (now < self.force_run_until or dist > FOLLOW_FAST_DIST) else 3
                step  = speed if dx > 0 else -speed
                nx    = max(left_edge, min(right_edge, g.x() + step))
                self.move(nx, bottom)
                if dist > FOLLOW_RUN_DIST:
                    want = "run_right" if dx > 0 else "run_left"
                else:
                    want = "walk_right" if dx > 0 else "walk_left"
                if want != self.current_action:
                    self.set_action(want)
            self.last_cursor_pos = mp; self.last_cursor_t = now
            return

        # 6) 랜덤 이동
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
            self.move(nx, bottom)
            want = "walk_right" if self.vx > 0 else "walk_left"
            if want != self.current_action:
                self.set_action(want)
            return

        # 7) 기본 idle
        if self.current_action != "idle":
            self.set_action("idle", force=True)
        else:
            self._ensure_on_floor()

# ==================== main ====================
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
