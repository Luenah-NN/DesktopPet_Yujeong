# -*- coding: utf-8 -*-
import sys, os, random, time
from collections import deque
from pathlib import Path
from PyQt5 import QtCore, QtGui, QtWidgets

# ================= 전역 설정 =================
CHAR_NAME = "Yujeong"
BG_MODE   = "rembg"     # "chroma" or "rembg"
SCALE     = 0.7
BASE_DIR  = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))

# 애니메이션 관련
MIN_FRAME_DELAY = 40      # 일부 GIF가 0ms로 되어있을 때 최소값
INITIAL_SYNC_FRAMES = 4   # 시작 몇 프레임은 강제로 사이즈 맞추기

# 물리/화면
WINDOW_PAD   = 2
EDGE_MARGIN  = 10
FLOOR_MARGIN = 2
GRAVITY      = 1.1
BOUNCE_K     = 0.78       # ✅ 탄성 살짝 더 크게
THROW_ANGRY_SPEED = 1200.0

# 마우스 따라가기 점프 떨림 방지용 히스테리시스
FOLLOW_JUMP_NEAR = 28
FOLLOW_JUMP_FAR  = 46
FOLLOW_RUN_DIST  = 200
FOLLOW_FAST_DIST = 400

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

FLOOR_SNAP_ACTIONS = {
    "dance","eat","pet","sleep","squat","boxing","plank","jumping_jacks"
}

def available_geo(window: QtWidgets.QWidget) -> QtCore.QRect:
    win = window.windowHandle()
    if win and win.screen():
        return win.screen().availableGeometry()
    scr = QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos())
    return scr.availableGeometry() if scr else QtWidgets.QApplication.primaryScreen().availableGeometry()


# =====================================================
# PetManager (충돌 회피 제거)
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
# Pet 본체 (사전 디코딩 버전)
# =====================================================
class Pet(QtWidgets.QMainWindow):
    def __init__(self, manager: PetManager):
        super().__init__()
        self.mgr = manager

        # ----- 창 설정 -----
        self.setWindowTitle(CHAR_NAME)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)
        self.setContentsMargins(0, 0, 0, 0)
        self.setWindowFlag(QtCore.Qt.FramelessWindowHint, True)
        self.setWindowFlag(QtCore.Qt.Tool, True)
        self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, True)

        icon_path = (BASE_DIR / "icons" / "icon.ico").as_posix()
        if os.path.exists(icon_path):
            self.setWindowIcon(QtGui.QIcon(icon_path))

        # ----- 라벨 -----
        self.label = QtWidgets.QLabel(self)
        self.label.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.label.setContentsMargins(0, 0, 0, 0)
        self.label.setScaledContents(False)
        self.setCentralWidget(self.label)

        # ----- 상태/물리 -----
        self.vx, self.vy = 0.0, 0.0
        self.dragging = False
        self.drag_offset = QtCore.QPoint(0, 0)
        self.press_pos = None
        self.drag_threshold = 6

        self.follow_mouse = False
        self.random_walk  = False
        self.stop_move    = False
        self.mode         = "normal"
        self.menu_open    = False

        # 마우스 근접 jump 떨림 방지
        self.follow_near_lock = False
        self.jump_cooldown_until = 0.0

        # 운동 모드
        self.exercise_cycle = ["squat","boxing","plank","jumping_jacks"]
        self.exercise_idx = 0
        self.exercise_timer = QtCore.QTimer(self)
        self.exercise_timer.timeout.connect(self._exercise_next)

        # 임시 모션
        self.force_action_until = 0.0
        self.temp_token = 0
        self._temp_stop_saved = {}

        # 클릭
        self.single_click_timer = QtCore.QTimer(self)
        self.single_click_timer.setSingleShot(True)
        self.single_click_timer.timeout.connect(self._trigger_single_click)

        # 등반
        self.climb_hold_until = 0.0
        self.climb_hold_timer = None
        self.follow_resume_dir = 0
        self.follow_resume_deadline = 0.0
        self.force_run_until = 0.0

        # ====== 🔥 모든 모션 사전 디코딩 ======
        self.animations = {}     # action -> list of (QPixmap, delay_ms)
        self.anim_max_size = {}  # action -> (w, h)
        self.global_max_w = 1
        self.global_max_h = 1

        self._predecode_all()

        # 현재 애니 상태
        self.current_action = None
        self.current_frame_idx = 0
        self.next_frame_time = time.monotonic()
        self._sync_frames_left = INITIAL_SYNC_FRAMES

        # 메뉴
        self._make_menu()

        # 초기 모션: idle
        self.set_action("idle", force=True)

        # 메인 틱 (물리/로직)
        self.tick = QtCore.QTimer(self)
        self.tick.timeout.connect(self.update_loop)
        self.tick.start(16)  # 60fps 근사

        # 애니메이션 틱은 update_loop 안에서 now 기준으로 처리

        # 시작 위치
        scr = available_geo(self)
        start_x = scr.x() + max(40, scr.width() // 2 - self.width() // 2)
        start_y = scr.y() + 40
        self.move(start_x, start_y)
        self._clamp_to_screen()

        # 입력 기록
        self.drag_trace = deque(maxlen=6)

    # -------------------------------------------------
    # 모든 모션 사전 디코딩
    # -------------------------------------------------
    def _predecode_all(self):
        for action, rel_path in ACTIONS.items():
            path = (BASE_DIR / "assets" / CHAR_NAME / rel_path).as_posix()
            frames, delays, max_w, max_h = self._decode_gif(path)
            self.animations[action] = list(zip(frames, delays))
            self.anim_max_size[action] = (max_w, max_h)
            self.global_max_w = max(self.global_max_w, int(max_w * SCALE))
            self.global_max_h = max(self.global_max_h, int(max_h * SCALE))

        # 전부 읽었으니 창/라벨을 가장 큰 걸로 고정
        self.label.resize(self.global_max_w, self.global_max_h)
        self.setFixedSize(self.global_max_w + WINDOW_PAD, self.global_max_h + WINDOW_PAD)

    def _decode_gif(self, path):
        """GIF를 전부 프레임으로 풀어서 (scaled QPixmap 리스트, delay 리스트, max_w, max_h) 리턴"""
        if not os.path.exists(path):
            # 없는 경우 더미 하나
            pm = QtGui.QPixmap(64, 64)
            pm.fill(QtCore.Qt.transparent)
            return [pm], [200], 64, 64

        movie = QtGui.QMovie(path)
        frames = []
        delays = []
        max_w = 1
        max_h = 1

        frame_idx = 0
        while True:
            if not movie.jumpToFrame(frame_idx):
                break
            # 원본 픽스맵
            pix = movie.currentPixmap()
            if pix.isNull():
                break

            # 크기 기록
            w = pix.width()
            h = pix.height()
            max_w = max(max_w, w)
            max_h = max(max_h, h)

            # 스케일 적용 (사전 스케일 → 런타임 부담 ↓)
            if SCALE != 1.0:
                scaled = pix.scaled(int(w * SCALE), int(h * SCALE), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            else:
                scaled = pix

            frames.append(scaled)

            # delay
            d = movie.nextFrameDelay()
            if d <= 0:
                d = MIN_FRAME_DELAY
            delays.append(d / 1000.0)  # 초 단위로 저장

            frame_idx += 1

        if not frames:
            pm = QtGui.QPixmap(64, 64)
            pm.fill(QtCore.Qt.transparent)
            return [pm], [0.2], 64, 64

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
        self.act_eat    = self.menu.addAction("간식주기 (10초)")
        self.act_pet    = self.menu.addAction("쓰다듬기 (10초)")
        self.act_ex     = self.menu.addAction("운동하기 (토글)")
        self.act_sleep  = self.menu.addAction("잠자기 (토글)")
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

        if action == self.act_follow:
            self.follow_mouse = not self.follow_mouse
            if self.follow_mouse:
                self.random_walk = False

        elif action == self.act_random:
            self.random_walk = not self.random_walk
            if self.random_walk:
                self.follow_mouse = False

        elif action == self.act_dance:
            if self.mode == "dance":
                self.mode = "normal"; self.stop_move = False
                self.set_action("idle")
            else:
                self._exit_modes()
                self.mode = "dance"; self.stop_move = True
                self.set_action("dance", force=True)

        elif action == self.act_ex:
            if self.mode == "exercise":
                self._exit_modes()
                self.set_action("idle")
            else:
                self._exit_modes()
                self.mode = "exercise"; self.stop_move = True
                first = random.choice(self.exercise_cycle)
                self.set_action(first, force=True)
                self.exercise_idx = self.exercise_cycle.index(first)
                self.exercise_timer.start(10_000)

        elif action == self.act_sleep:
            if self.mode == "sleep":
                self._exit_modes()
                self.set_action("idle")
            else:
                self._exit_modes()
                self.mode = "sleep"; self.stop_move = True
                self.set_action("sleep", force=True)

        elif action == self.act_eat:
            self._exit_modes()
            self.play_temp("eat", 10_000, stop_during_temp=True)

        elif action == self.act_pet:
            self._exit_modes()
            self.play_temp("pet", 10_000, stop_during_temp=True)

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
                if self.mode == "normal":
                    self.set_action("hang")
        if self.dragging:
            self._record_drag_point(ev.globalPos())
            self.move(ev.globalPos() - self.drag_offset)
            self._clamp_to_screen()
            g = self.geometry(); scr = available_geo(self)
            if g.x() <= scr.x() + EDGE_MARGIN:
                self._enter_climb("left")
            elif g.x() >= scr.x() + scr.width() - self.width() - EDGE_MARGIN:
                self._enter_climb("right")

    def mouseReleaseEvent(self, ev):
        if ev.button() != QtCore.Qt.LeftButton:
            return
        if self.dragging:
            self.dragging = False
            self._apply_throw_velocity()
            g = self.geometry(); scr = available_geo(self)
            if self.mode in ("dance","exercise","sleep"):
                return
            if g.x() <= scr.x() + EDGE_MARGIN:
                self._enter_climb("left"); return
            if g.x() >= scr.x() + scr.width() - self.width() - EDGE_MARGIN:
                self._enter_climb("right"); return
            if self.current_action != "hang":
                self.set_action("hang")
            self.vy = max(self.vy, 2.5)

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            self.single_click_timer.stop()
            self.play_temp("angry", 5000)

    def _trigger_single_click(self):
        self.play_temp("surprise", 5000)

    # -------------------------------------------------
    # 드래그 속도 → 던지기
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
        if speed > THROW_ANGRY_SPEED:
            self.play_temp("angry", 2000)

    # -------------------------------------------------
    # 임시 모션
    # -------------------------------------------------
    def play_temp(self, key, hold_ms, on_done=None, stop_during_temp=False):
        self.temp_token += 1
        token = self.temp_token
        self.set_action(key, force=True)
        if stop_during_temp:
            self._temp_stop_saved[token] = self.stop_move
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
        if token in self._temp_stop_saved:
            self.stop_move = self._temp_stop_saved.pop(token)
        if self.mode in ("dance","exercise","sleep"):
            return
        if self.follow_mouse or self.random_walk:
            return
        self.set_action("idle")

    # -------------------------------------------------
    # 액션/애니 전환 (사전 디코딩 사용)
    # -------------------------------------------------
    def set_action(self, key, force=False):
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
            self._apply_frame(frames[0][0])
        else:
            self.next_frame_time = now + 0.2

        # 바닥 스냅
        if key in FLOOR_SNAP_ACTIONS:
            self._snap_floor()

    def _apply_frame(self, pix: QtGui.QPixmap):
        # label/window는 이미 global max로 잡혀있음 → 그냥 pix만 교체
        self.label.setPixmap(pix)
        if BG_MODE == "chroma":
            self.setMask(pix.createMaskFromColor(QtGui.QColor(255,255,255), QtCore.Qt.MaskOutColor))
        else:
            self.clearMask()

    # -------------------------------------------------
    # 애니메이션 한 프레임 진행
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
        self._apply_frame(pix)
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
        bottom_y = scr.y() + scr.height() - self.height() - FLOOR_MARGIN
        self.move(self.x(), bottom_y)

    def _enter_climb(self, side: str):
        if side == "left":
            self.set_action("climb_left", force=True); self.follow_resume_dir = 1
        else:
            self.set_action("climb_right", force=True); self.follow_resume_dir = -1
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
            self.set_action("hang")
            self.vy = max(self.vy, 2.0)
            self.follow_resume_deadline = time.monotonic() + 1.5
            self.force_run_until = time.monotonic() + 0.8

    # -------------------------------------------------
    # 메인 루프
    # -------------------------------------------------
    def update_loop(self):
        now = time.monotonic()

        # 먼저 애니 진행
        self._update_animation(now)

        if self.menu_open:
            return
        if self.mode in ("dance","exercise","sleep"):
            # 이런 모드들은 제자리 애니만
            return

        g = self.geometry()
        scr = available_geo(self)
        left_edge = scr.x()
        right_edge = scr.x() + scr.width() - self.width()
        bottom = scr.y() + scr.height() - self.height()

        in_climb = self.current_action in ("climb_left","climb_right")

        # 1) 중력
        if not self.stop_move and not self.dragging:
            if g.y() < bottom and not in_climb:
                self.vy += GRAVITY
                ny = min(bottom, g.y() + int(self.vy))
                self.move(g.x(), ny)
                if ny >= bottom:
                    # 착지
                    if abs(self.vy) > 3.5:
                        self.vy = -abs(self.vy) * BOUNCE_K   # ✅ 탄성 증가
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

        # 2) 등반 유지
        if in_climb:
            if self.climb_hold_until and now >= self.climb_hold_until:
                self._end_climb_hold()
            return

        # 3) 마우스 따라가기
        if self.follow_mouse:
            mp = QtGui.QCursor.pos()
            cx = g.x() + self.width() // 2
            dx = mp.x() - cx
            dist = abs(dx)

            # 커서가 가까워졌을 때 jump가 덜덜 떨리는 문제 → 히스테리시스
            if dist <= FOLLOW_JUMP_NEAR and now >= self.jump_cooldown_until:
                if not self.follow_near_lock:
                    self.set_action("jump", force=True)
                    self.follow_near_lock = True
                    self.jump_cooldown_until = now + 0.35  # 0.35초는 다시 점프 안 함
                return
            elif dist >= FOLLOW_JUMP_FAR:
                self.follow_near_lock = False

            # 바닥에서만 걷고 뛴다
            if g.y() >= bottom:
                # 속도 결정
                speed = 6 if (now < self.force_run_until or dist > FOLLOW_FAST_DIST) else 3
                step = speed if dx > 0 else -speed
                nx = g.x() + step
                nx = max(left_edge, min(right_edge, nx))
                self.move(nx, g.y())

                # 액션은 방향 바뀔 때만
                if dist > FOLLOW_RUN_DIST:
                    want = "run_right" if dx > 0 else "run_left"
                else:
                    want = "walk_right" if dx > 0 else "walk_left"
                if want != self.current_action:
                    self.set_action(want)

            return

        # 4) 랜덤 이동
        if self.random_walk:
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

        # 5) 기본은 idle
        if self.current_action != "idle":
            self.set_action("idle")

# =====================================================
# main
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
