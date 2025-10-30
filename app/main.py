# -*- coding: utf-8 -*-
import sys, os, random, time
from collections import deque
from pathlib import Path
from PyQt5 import QtCore, QtGui, QtWidgets

# ================== 전역 설정 ==================
CHAR_NAME = "Yujeong"
BG_MODE   = "rembg"     # "chroma" or "rembg"
SCALE     = 0.7         # 모든 모션 공통 스케일
BASE_DIR  = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))

INITIAL_SYNC_FRAMES = 8       # GIF 잘림 방지: 초반 N프레임 강제 동기화
WINDOW_PAD          = 2       # 라벨보다 창이 항상 약간 더 크도록
EDGE_MARGIN         = 10
FLOOR_MARGIN        = 2
CLIMB_TO_RUN_FLOOR_NEAR = 20

# 세게 던졌을 때 angry로 바꾸는 임계속도(px/sec)
THROW_ANGRY_SPEED   = 1200.0

# 자주 쓰는 액션들: 캐시 크게
CACHE_HEAVY_ACTIONS = {
    "idle", "walk_left", "walk_right",
    "run_left", "run_right",
    "climb_left", "climb_right",
    "hang"
}

# 액션 → 파일 경로
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

# 바닥 스냅 대상(작업표시줄 위에 앉아야 하는 것들)
FLOOR_SNAP_ACTIONS = {
    "dance","eat","pet","sleep","squat","boxing","plank","jumping_jacks"
}

# ---------- 화면 크기 얻기 ----------
def available_geo(window: QtWidgets.QWidget) -> QtCore.QRect:
    win = window.windowHandle()
    if win and win.screen():
        return win.screen().availableGeometry()
    scr = QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos())
    return scr.availableGeometry() if scr else QtWidgets.QApplication.primaryScreen().availableGeometry()

# =====================================================
# PetManager: 여러 마리 관리 + 충돌 회피
# =====================================================
class PetManager(QtCore.QObject):
    MAX_PETS = 8

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.pets = []

        # 멀티펫 충돌 회피용 타이머
        self.collide_timer = QtCore.QTimer(self)
        self.collide_timer.timeout.connect(self.resolve_collisions)
        self.collide_timer.start(200)  # 0.2초마다 한 번 정렬

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

    def resolve_collisions(self):
        """여러 마리가 겹쳐 있으면 좌우로 살짝 밀어냄."""
        pets = self.pets
        for i in range(len(pets)):
            for j in range(i + 1, len(pets)):
                a = pets[i]; b = pets[j]
                ag = a.geometry(); bg = b.geometry()
                if ag.intersects(bg):
                    # 가로 겹침량 계산
                    overlap = min(ag.right(), bg.right()) - max(ag.left(), bg.left())
                    if overlap <= 0:
                        continue
                    shift = overlap // 2 + 2
                    if ag.center().x() <= bg.center().x():
                        a.move(ag.x() - shift, ag.y())
                        b.move(bg.x() + shift, bg.y())
                    else:
                        a.move(ag.x() + shift, ag.y())
                        b.move(bg.x() - shift, bg.y())
                    a._clamp_to_screen()
                    b._clamp_to_screen()

# =====================================================
# Pet 본체
# =====================================================
class Pet(QtWidgets.QMainWindow):
    HOLD_SHORT = 1200
    HOLD_MED   = 2000

    def __init__(self, manager: PetManager):
        super().__init__()
        self.mgr = manager

        # ---- 기본 창 설정 ----
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

        # ---- 라벨 ----
        self.label = QtWidgets.QLabel(self)
        self.label.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.label.setContentsMargins(0, 0, 0, 0)
        self.label.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.label.setScaledContents(False)
        self.setCentralWidget(self.label)

        # ---- 리소스 ----
        self.anim_paths = {
            k: (BASE_DIR / "assets" / CHAR_NAME / v).as_posix()
            for k, v in ACTIONS.items()
        }
        self.movie = None
        self.current_action = None
        self._sync_frames_left = 0

        # ---- GIF 최대 프레임 크기 선계산 ----
        self.action_max_size = {}
        for key, path in self.anim_paths.items():
            self.action_max_size[key] = self._probe_gif(path)

        # ---- 물리/상태 ----
        self.vx, self.vy = 0.0, 0.0
        self.ay = 1.1
        self.dragging = False
        self.drag_offset = QtCore.QPoint(0, 0)
        self.press_pos = None
        self.drag_threshold = 6

        self.follow_mouse = False
        self.random_walk  = False
        self.stop_move    = False
        self.always_active = True
        self.mode = "normal"
        self.menu_open = False

        self.exercise_cycle = ["squat", "boxing", "plank", "jumping_jacks"]
        self.exercise_idx = 0
        self.exercise_timer = QtCore.QTimer(self)
        self.exercise_timer.timeout.connect(self._exercise_next)

        # 임시 모션 강제
        self.force_action_until = 0.0
        self.temp_token = 0
        self._temp_stop_saved = {}

        # 클릭 관련
        self.single_click_timer = QtCore.QTimer(self)
        self.single_click_timer.setSingleShot(True)
        self.single_click_timer.timeout.connect(self._trigger_single_click)

        # 팔로우/등반 관련
        self.force_run_until = 0.0
        self.near_dist = 28
        self.follow_resume_dir = 0
        self.follow_resume_deadline = 0.0
        self.climb_hold_until = 0.0
        self.climb_hold_timer = None

        # 작업표시줄 걷기 방향
        self.tb_dir = 1

        # 메인 틱
        self.tick = QtCore.QTimer(self)
        self.tick.timeout.connect(self.update_loop)
        self.tick.start(16)

        self.make_menu()

        # 초기 모션
        self.set_action("idle", force=True)
        self._warmup_current_movie(60)
        self.movie.jumpToFrame(0)
        self._sync_window_to_pixmap()

        # 시작 위치
        scr = available_geo(self)
        start_x = scr.x() + max(40, scr.width() // 2 - self.width() // 2)
        start_y = scr.y() + 40
        self.move(start_x, start_y)
        self._clamp_to_screen()
        self.vy = 0.0

        # 입력 기록
        self.click_times = deque(maxlen=8)
        self.click_window = 0.9
        self.drag_trace = deque(maxlen=6)

    # -------------------------------------------------
    # GIF 최대 크기 미리 뽑기
    # -------------------------------------------------
    def _probe_gif(self, path, max_frames=120):
        """GIF 전체 프레임 중 가장 큰 (w,h)를 찾아서 리턴."""
        if not os.path.exists(path):
            return (0, 0)
        movie = QtGui.QMovie(path)
        movie.jumpToFrame(0)
        rect = movie.frameRect()
        max_w = rect.width()
        max_h = rect.height()
        for i in range(1, max_frames):
            if not movie.jumpToFrame(i):
                break
            r = movie.frameRect()
            if r.width() > max_w:
                max_w = r.width()
            if r.height() > max_h:
                max_h = r.height()
        movie.stop()
        return (max_w, max_h)

    # -------------------------------------------------
    # 메뉴
    # -------------------------------------------------
    def make_menu(self):
        self.menu = QtWidgets.QMenu(self)
        self.act_follow = self.menu.addAction("마우스 따라가기")
        self.act_random = self.menu.addAction("랜덤 이동")
        self.menu.addSeparator()
        self.act_dance  = self.menu.addAction("춤추기 (토글)")
        self.act_eat    = self.menu.addAction("간식주기 (10초)")
        self.act_pet    = self.menu.addAction("쓰다듬기 (10초)")
        self.act_ex     = self.menu.addAction("운동하기 (토글, 10초 간격)")
        self.act_sleep  = self.menu.addAction("잠자기 (토글)")
        self.menu.addSeparator()
        self.act_spawn  = self.menu.addAction("펫 추가")
        self.act_close  = self.menu.addAction("이 펫 닫기")
        self.menu.addSeparator()
        self.act_always = self.menu.addAction("항상 활성화")

        for a in [
            self.act_follow, self.act_random, self.act_always,
            self.act_dance, self.act_ex, self.act_sleep
        ]:
            a.setCheckable(True)
        self._refresh_checks()

    def _refresh_checks(self):
        self.act_follow.setChecked(self.follow_mouse)
        self.act_random.setChecked(self.random_walk)
        self.act_always.setChecked(self.always_active)
        self.act_dance.setChecked(self.mode == "dance")
        self.act_ex.setChecked(self.mode == "exercise")
        self.act_sleep.setChecked(self.mode == "sleep")

    def _exit_modes(self):
        if self.mode == "exercise":
            self.exercise_timer.stop()
        self.mode = "normal"
        self._refresh_checks()

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
                if self.vx == 0:
                    self.vx = random.choice([-2.0, 2.0])

        elif action == self.act_dance:
            self._exit_modes()
            if self.mode != "dance":
                self.mode = "dance"; self.stop_move = True; self.set_action("dance", force=True)
            else:
                self.stop_move = False; self.mode = "normal"

        elif action == self.act_ex:
            if self.mode == "exercise":
                self.exercise_timer.stop(); self.mode = "normal"; self.stop_move = False
            else:
                self._exit_modes()
                self.mode = "exercise"; self.stop_move = True
                first = random.choice(self.exercise_cycle)
                self.set_action(first, force=True)
                self.exercise_idx = self.exercise_cycle.index(first)
                self.exercise_timer.start(10_000)

        elif action == self.act_sleep:
            if self.mode == "sleep":
                self.mode = "normal"; self.stop_move = False
            else:
                self._exit_modes()
                self.mode = "sleep"; self.stop_move = True; self.set_action("sleep", force=True)

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
                    self.set_action("hang", force=True)
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
                # 모드 유지
                if self.mode == "dance": self.set_action("dance", force=True)
                elif self.mode == "sleep": self.set_action("sleep", force=True)
                return
            if g.x() <= scr.x() + EDGE_MARGIN:
                self._enter_climb("left"); return
            if g.x() >= scr.x() + scr.width() - self.width() - EDGE_MARGIN:
                self._enter_climb("right"); return
            if self.current_action != "hang":
                self.set_action("hang", force=True)
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

        # 💥 세게 던졌을 때만 angry
        speed = ((dx ** 2 + dy ** 2) ** 0.5) / dt
        if speed > THROW_ANGRY_SPEED:
            self.play_temp("angry", 2000)

    # -------------------------------------------------
    # 임시 오버라이드 모션
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
        self.set_action("idle", force=True)

    # -------------------------------------------------
    # 액션/사이즈 동기화
    # -------------------------------------------------
    def set_action(self, key, force=False):
        # 같은 액션이라도 force=True면 다시 로드
        if not force and key == self.current_action:
            return

        path = self.anim_paths.get(key)
        if not path or not os.path.exists(path):
            return

        self.current_action = key
        self.movie = QtGui.QMovie(path)

        # 캐시 모드 선택
        if key in CACHE_HEAVY_ACTIONS:
            self.movie.setCacheMode(QtGui.QMovie.CacheAll)
        else:
            self.movie.setCacheMode(QtGui.QMovie.CacheNone)

        # 논리 캔버스 기준 스케일
        logical = self.movie.frameRect().size()
        scaled = QtCore.QSize(int(round(logical.width() * SCALE)),
                              int(round(logical.height() * SCALE)))
        if scaled.width() > 0 and scaled.height() > 0:
            self.movie.setScaledSize(scaled)

        self.label.setMovie(self.movie)
        try:
            self.movie.frameChanged.disconnect()
        except Exception:
            pass
        self.movie.frameChanged.connect(self._on_frame_changed)

        self._sync_frames_left = INITIAL_SYNC_FRAMES
        self.movie.start()
        self.movie.jumpToFrame(0)
        self._sync_window_to_pixmap()

        if key in FLOOR_SNAP_ACTIONS:
            self._snap_floor()
            QtCore.QTimer.singleShot(0, self._snap_floor)

        self._clamp_to_screen()
        QtCore.QTimer.singleShot(0, self.resize_to_movie)

    def _warmup_current_movie(self, ms):
        t0 = time.monotonic()
        while (time.monotonic() - t0) < (ms / 1000.0):
            QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 5)

    def resize_to_movie(self):
        if not self.movie:
            return
        logical = self.movie.frameRect().size()
        w = int(round(logical.width() * SCALE))
        h = int(round(logical.height() * SCALE))
        if w > 0 and h > 0:
            self.label.resize(w, h)
            self.setFixedSize(w + WINDOW_PAD, h + WINDOW_PAD)
        # exe에서 잘리는 경우 대비
        self._sync_window_to_pixmap()

    def _sync_window_to_pixmap(self):
        """현재 프레임의 실제 픽셀, 우리가 의도한 스케일, 그리고 선계산한 최대 프레임 중 제일 큰 걸로 라벨/창 크기를 맞춘다."""
        if not self.movie:
            return

        # 1) 현재 프레임
        pix = self.movie.currentPixmap()
        # 2) 현재 논리 프레임
        logical = self.movie.frameRect().size()

        # 우리가 의도한 크기
        want_w = int(round(logical.width() * SCALE))
        want_h = int(round(logical.height() * SCALE))

        # 선계산한 최대 프레임 크기
        if self.current_action in self.action_max_size:
            max_w0, max_h0 = self.action_max_size[self.current_action]
            want_w = max(want_w, int(round(max_w0 * SCALE)))
            want_h = max(want_h, int(round(max_h0 * SCALE)))

        if pix.isNull():
            w = max(1, want_w)
            h = max(1, want_h)
        else:
            dpr = pix.devicePixelRatio() or 1.0
            pix_w = int(round(pix.width() / dpr))
            pix_h = int(round(pix.height() / dpr))
            w = max(1, pix_w, want_w)
            h = max(1, pix_h, want_h)

        self.label.resize(w, h)
        self.setFixedSize(w + WINDOW_PAD, h + WINDOW_PAD)

    def _on_frame_changed(self, _i):
        if self._sync_frames_left > 0:
            self._sync_window_to_pixmap()
            self._sync_frames_left -= 1

        if BG_MODE == "chroma":
            pix = self.movie.currentPixmap()
            self.setMask(pix.createMaskFromColor(QtGui.QColor(255,255,255), QtCore.Qt.MaskOutColor))
        else:
            self.clearMask()

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
        cur_x = self.x()
        if cur_x < scr.x():
            cur_x = scr.x() + 40
        self.move(cur_x, bottom_y)

    def _enter_climb(self, side: str):
        if side == "left":
            self.set_action("climb_left", force=True); self.follow_resume_dir = 1
        else:
            self.set_action("climb_right", force=True); self.follow_resume_dir = -1
        self.climb_hold_until = time.monotonic() + 10.0
        if getattr(self, "climb_hold_timer", None):
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

    # -------------------------------------------------
    # 메인 루프
    # -------------------------------------------------
    def update_loop(self):
        now = time.monotonic()
        if self.menu_open:
            return
        if self.mode in ("dance","exercise","sleep"):
            return

        g = self.geometry()
        scr = available_geo(self)
        left_edge = scr.x()
        right_edge = scr.x() + scr.width() - self.width()
        bottom = scr.y() + scr.height() - self.height()

        in_climb = self.current_action in ("climb_left","climb_right")

        # 0) 화면 위쪽에 닿으면 자동 매달리기
        if (not self.dragging) and (not self.stop_move) and (not in_climb) and g.y() <= scr.y() + EDGE_MARGIN:
            # 위에 달라붙게
            self.move(g.x(), scr.y())
            if self.current_action != "hang":
                self.set_action("hang", force=True)
            return

        # 1) 중력
        if (not self.stop_move) and (not self.dragging) and (g.y() < bottom) and (not in_climb):
            self.vy += self.ay
            ny = min(bottom, g.y() + int(self.vy))
            self.move(g.x(), ny)
            if ny >= bottom:
                if abs(self.vy) > 3.5:
                    self.vy = -abs(self.vy) * 0.60
                    self.vx *= 0.9
                else:
                    self.vy = 0.0
                    if self.follow_mouse:
                        pass
                    elif self.random_walk:
                        if self.vx == 0:
                            self.vx = random.choice([-2.0, 2.0])
                        self.set_action("walk_right" if self.vx > 0 else "walk_left", force=True)
                    else:
                        self.set_action("idle", force=True)
            return

        # 2) 등반 중인 경우 처리
        if in_climb:
            if self.follow_mouse:
                mp = QtGui.QCursor.pos()
                if (bottom - g.y()) <= CLIMB_TO_RUN_FLOOR_NEAR:
                    dx = mp.x() - (g.x() + self.width() // 2)
                    self.vy = 0.0
                    self.move(g.x(), bottom)
                    self.set_action("run_right" if dx > 0 else "run_left", force=True)
                    self.force_run_until = now + 0.8
                    return
                target_y = mp.y() - self.height() // 2
                dy = 0
                if abs(g.y() - target_y) > 2:
                    dy = -2 if g.y() > target_y else 2
                ny = max(scr.y(), min(bottom, g.y() + dy))
                self.move(g.x(), ny)
                if (self.current_action == "climb_left" and g.x() > left_edge + EDGE_MARGIN) or \
                   (self.current_action == "climb_right" and g.x() < right_edge - EDGE_MARGIN):
                    self._end_climb_hold()
                return
            else:
                dy = -1 if (QtCore.QTime.currentTime().msec() // 500) % 2 == 0 else 1
                ny = max(scr.y(), min(bottom, g.y() + dy))
                self.move(g.x(), ny)
            if self.climb_hold_until and now >= self.climb_hold_until:
                self._end_climb_hold()
            return

        if self.stop_move or self.dragging:
            return

        # 3) 마우스 팔로우
        if self.follow_mouse:
            mp = QtGui.QCursor.pos()
            cx = g.x() + self.width() // 2
            dx = mp.x() - cx
            dist = abs(dx)
            if dist <= 28 or g.contains(mp):
                if self.current_action != "jump":
                    self.set_action("jump", force=True)
                return
            if g.x() <= left_edge + EDGE_MARGIN:
                self._enter_climb("left"); return
            if g.x() >= right_edge - EDGE_MARGIN:
                self._enter_climb("right"); return

            if self.follow_resume_deadline > now and self.current_action not in ("climb_left","climb_right","jump"):
                self.set_action("run_right" if self.follow_resume_dir > 0 else "run_left", force=True)

            speed = 6 if (now < self.force_run_until or dist > 400) else 3
            step = speed if dx > 0 else -speed
            nx = g.x() + step
            if abs((nx + self.width() // 2) - mp.x()) < speed:
                nx = mp.x() - self.width() // 2
            nx = max(left_edge, min(right_edge, nx))
            self.move(nx, g.y())
            if (now < self.force_run_until) or dist > 200:
                self.set_action("run_right" if dx > 0 else "run_left", force=True)
            else:
                self.set_action("walk_right" if dx > 0 else "walk_left", force=True)
            self.check_bounce()
            return

        # 4) 랜덤 워크
        if self.random_walk:
            if self.vx == 0:
                self.vx = random.choice([-2.0, 2.0])
            nx = g.x() + int(self.vx)
            if nx <= left_edge:
                nx = left_edge; self.vx = abs(self.vx); self.check_bounce()
            elif nx >= right_edge:
                nx = right_edge; self.vx = -abs(self.vx); self.check_bounce()
            self.move(nx, g.y())
            self.set_action("walk_right" if self.vx > 0 else "walk_left", force=True)
            return

        # 5) 작업표시줄 위 걷기 (바닥에 붙어 있고, 다른 모드/팔로우 아님)
        if g.y() >= bottom and (not self.follow_mouse) and (not self.random_walk):
            nx = g.x() + self.tb_dir * 1
            if nx <= left_edge:
                nx = left_edge
                self.tb_dir = 1
            elif nx >= right_edge:
                nx = right_edge
                self.tb_dir = -1
            self.move(nx, g.y())
            self.set_action("walk_right" if self.tb_dir > 0 else "walk_left", force=True)
            return

        # 6) 그 외엔 idle
        self.set_action("idle", force=True)

    # -------------------------------------------------
    def check_bounce(self):
        g = self.geometry(); scr = available_geo(self)
        hit_left  = g.x() <= scr.x()
        hit_right = g.x() + self.width() >= (scr.x() + scr.width())
        if hit_left or hit_right:
            self.vx = -self.vx if self.vx != 0 else self.vx
            self.vy = -10
            self.set_action("jump", force=True)
            nx = scr.x()+1 if hit_left else scr.x()+scr.width()-self.width()-1
            self.move(nx, g.y())
            QtCore.QTimer.singleShot(600, self._end_bounce)

    def _end_bounce(self):
        if self.follow_mouse:
            self.set_action("run_right" if self.vx > 0 else "run_left", force=True)
        elif self.random_walk:
            self.set_action("walk_right" if self.vx > 0 else "walk_left", force=True)
        else:
            self.set_action("idle", force=True)

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
