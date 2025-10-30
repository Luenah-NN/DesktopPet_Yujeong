# -*- coding: utf-8 -*-
import sys, os, random, time
from collections import deque
from pathlib import Path
from PyQt5 import QtCore, QtGui, QtWidgets

CHAR_NAME = "Yujeong"
BG_MODE   = "rembg"     # "chroma" or "rembg"
SCALE     = 0.7        
BASE_DIR  = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))

# GIF 잘림 방지용: 초반 몇 프레임 동안 실제 픽스맵 크기에 맞춰 라벨/창 크기 재동기화
INITIAL_SYNC_FRAMES = 8

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

# 바닥 스냅 대상(작업표시줄 위)
FLOOR_SNAP_ACTIONS = {
    "dance","eat","pet","sleep","squat","boxing","plank","jumping_jacks"
}
EDGE_MARGIN   = 10
FLOOR_MARGIN  = 2
CLIMB_TO_RUN_FLOOR_NEAR = 20

def available_geo(window: QtWidgets.QWidget) -> QtCore.QRect:
    win = window.windowHandle()
    if win and win.screen():
        return win.screen().availableGeometry()
    scr = QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos())
    return scr.availableGeometry() if scr else QtWidgets.QApplication.primaryScreen().availableGeometry()

class PetManager(QtCore.QObject):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.pets = []

    def spawn(self, pos=None):
        pet = Pet(self)
        self.pets.append(pet)
        if pos is not None:
            pet.move(pos)
        pet.show()
        return pet

    def remove(self, pet):
        try: self.pets.remove(pet)
        except ValueError: pass
        pet.close()
        if not self.pets:
            QtCore.QTimer.singleShot(0, self.app.quit)

class Pet(QtWidgets.QMainWindow):
    HOLD_SHORT = 1200
    HOLD_MED   = 2000

    def __init__(self, manager: PetManager):
        super().__init__()
        self.mgr = manager

        # 창/위젯
        self.setWindowTitle(CHAR_NAME)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)
        self.setContentsMargins(0,0,0,0)
        self.setWindowFlag(QtCore.Qt.FramelessWindowHint, True)
        self.setWindowFlag(QtCore.Qt.Tool, True)
        self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, True)

        icon_path = (BASE_DIR / "icons" / "icon.ico").as_posix()
        if os.path.exists(icon_path):
            self.setWindowIcon(QtGui.QIcon(icon_path))

        self.label = QtWidgets.QLabel(self)
        self.label.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.label.setContentsMargins(0,0,0,0)
        self.label.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.label.setScaledContents(False)  # QMovie가 스케일 관리

        self.setCentralWidget(self.label)

        # 리소스
        self.anim_paths = {k: (BASE_DIR / "assets" / CHAR_NAME / v).as_posix() for k, v in ACTIONS.items()}
        self.movie = None
        self.current_action = None
        self._sync_frames_left = 0  # ✅ 초반 프레임 동기화 카운터

        # 물리
        self.vx, self.vy = 0.0, 0.0
        self.ay = 1.1
        self.dragging = False
        self.drag_offset = QtCore.QPoint(0, 0)
        self.press_pos = None
        self.drag_threshold = 6

        # 상태/모드
        self.follow_mouse = False
        self.random_walk  = False
        self.stop_move    = False
        self.always_active = True
        self.mode = "normal"
        self.menu_open = False

        # 운동
        self.exercise_cycle = ["squat","boxing","plank","jumping_jacks"]
        self.exercise_idx = 0
        self.exercise_timer = QtCore.QTimer(self)
        self.exercise_timer.timeout.connect(self._exercise_next)

        # 임시 오버라이드
        self.force_action_until = 0.0
        self.temp_token = 0
        self._temp_stop_saved = {}

        # 클릭(싱글/더블 분기)
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

        # 메인 루프
        self.tick = QtCore.QTimer(self)
        self.tick.timeout.connect(self.update_loop)
        self.tick.start(16)

        self.make_menu()

        # 초기 액션/워밍업/공중 시작
        self.set_action("idle")
        self._warmup_current_movie(120)
        self.movie.jumpToFrame(0)
        self._sync_window_to_pixmap()  # ✅ 즉시 한 번 동기화

        # 시작 위치(공중)
        scr = available_geo(self)
        start_x = scr.x() + max(40, scr.width()//2 - self.width()//2)
        start_y = scr.y() + 40
        self.move(start_x, start_y)
        self._clamp_to_screen()
        self.vy = 0.0

        # 입력/드래그 기록
        self.click_times = deque(maxlen=8)
        self.click_window = 0.9
        self.drag_trace = deque(maxlen=6)

    # ---------- 메뉴 (이전과 동일 로직) ----------
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

        for a in [self.act_follow, self.act_random, self.act_always,
                  self.act_dance, self.act_ex, self.act_sleep]:
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
                self.mode = "dance"; self.stop_move = True; self.set_action("dance")
            else:
                self.stop_move = False; self.mode = "normal"

        elif action == self.act_ex:
            if self.mode == "exercise":
                self.exercise_timer.stop(); self.mode = "normal"; self.stop_move = False
            else:
                self._exit_modes()
                self.mode = "exercise"; self.stop_move = True
                first = random.choice(self.exercise_cycle)
                self.set_action(first)
                self.exercise_idx = self.exercise_cycle.index(first)
                self.exercise_timer.start(10_000)

        elif action == self.act_sleep:
            if self.mode == "sleep":
                self.mode = "normal"; self.stop_move = False
            else:
                self._exit_modes()
                self.mode = "sleep"; self.stop_move = True; self.set_action("sleep")

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

    def _exercise_next(self):
        if self.mode != "exercise":
            self.exercise_timer.stop(); return
        self.exercise_idx = (self.exercise_idx + 1) % len(self.exercise_cycle)
        self.set_action(self.exercise_cycle[self.exercise_idx])

    # ---------- 입력(핵심 로직은 이전 버전과 동일) ----------
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
                if self.mode == "dance": self.set_action("dance")
                elif self.mode == "sleep": self.set_action("sleep")
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

    # ---------- 드래그 속도 ----------
    def _record_drag_point(self, gpos: QtCore.QPoint):
        self.drag_trace.append((QtCore.QPoint(gpos), time.monotonic()))

    def _apply_throw_velocity(self):
        if len(self.drag_trace) < 2: return
        (p2, t2) = self.drag_trace[-1]; (p1, t1) = self.drag_trace[0]
        dt = max(1e-3, (t2 - t1))
        dx, dy = p2.x()-p1.x(), p2.y()-p1.y()
        frames = dt / 0.016
        self.vx = dx / max(1.0, frames)
        self.vy = dy / max(1.0, frames)

    # ---------- 임시 오버라이드 ----------
    def play_temp(self, key, hold_ms, on_done=None, stop_during_temp=False):
        self.temp_token += 1
        token = self.temp_token
        self.set_action(key)
        if stop_during_temp:
            self._temp_stop_saved[token] = self.stop_move
            self.stop_move = True
        self.force_action_until = time.monotonic() + (hold_ms / 1000.0)
        def _end():
            if on_done: on_done()
            self._end_temp(token)
        QtCore.QTimer.singleShot(hold_ms, _end)

    def _end_temp(self, token):
        if token != self.temp_token: return
        self.force_action_until = 0.0
        if token in self._temp_stop_saved:
            self.stop_move = self._temp_stop_saved.pop(token)
        if self.mode in ("dance","exercise","sleep"):
            return
        if self.follow_mouse or self.random_walk:
            return
        self.set_action("idle")

    # ---------- 액션/사이즈 동기화(✅ 잘림 방지 핵심) ----------
    def set_action(self, key):
        if key == self.current_action:
            return
        path = self.anim_paths.get(key)
        if not path or not os.path.exists(path):
            return

        self.current_action = key
        self.movie = QtGui.QMovie(path)
        self.movie.setCacheMode(QtGui.QMovie.CacheAll)

        # 논리 캔버스 기준 스케일 지정
        logical = self.movie.frameRect().size()
        scaled  = QtCore.QSize(int(round(logical.width()*SCALE)),
                               int(round(logical.height()*SCALE)))
        if scaled.width() > 0 and scaled.height() > 0:
            self.movie.setScaledSize(scaled)

        self.label.setMovie(self.movie)
        try: self.movie.frameChanged.disconnect()
        except Exception: pass
        self.movie.frameChanged.connect(self._on_frame_changed)

        self._sync_frames_left = INITIAL_SYNC_FRAMES  # ✅ 초반 프레임 동기화 시작
        self.movie.start()
        self.movie.jumpToFrame(0)
        self._sync_window_to_pixmap()   # ✅ 즉시 1회

        # 바닥 스냅(작업표시줄 위)
        if key in FLOOR_SNAP_ACTIONS:
            self._snap_floor()
            QtCore.QTimer.singleShot(0, self._snap_floor)

        self._clamp_to_screen()
        QtCore.QTimer.singleShot(0, self.resize_to_movie)

    def _warmup_current_movie(self, ms):
        t0 = time.monotonic()
        while (time.monotonic() - t0) < (ms/1000.0):
            QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 5)

    def resize_to_movie(self):
        if not self.movie:
            return

        # QMovie가 실제론 원본 크기를 돌려줄 수 있으니,
        # 여기서도 SCALE을 다시 적용해준다.
        logical = self.movie.frameRect().size()
        w = int(round(logical.width()  * SCALE))
        h = int(round(logical.height() * SCALE))

        if w > 0 and h > 0:
            self.label.resize(w, h)
            self.setFixedSize(w, h)

    def _sync_window_to_pixmap(self):
        """현재 그려진 픽스맵의 실제 크기에 'SCALE'을 다시 적용해서 창/라벨을 맞춘다."""
        if not self.movie:
            return
        pix = self.movie.currentPixmap()
        if pix.isNull():
            return

        dpr = pix.devicePixelRatio() or 1.0
        base_w = pix.width()  / dpr
        base_h = pix.height() / dpr

        # QMovie.setScaledSize()가 안 먹는 환경(일부 Win/PyInstaller)에서도
        # 여기서 강제로 SCALE을 한 번 더 적용해버린다.
        w = max(1, int(round(base_w * SCALE)))
        h = max(1, int(round(base_h * SCALE)))

        self.label.resize(w, h)
        self.setFixedSize(w, h)

    def _on_frame_changed(self, _i):
        # ✅ 초반 몇 프레임 동안 실제 렌더 크기에 맞춰 재동기화
        if self._sync_frames_left > 0:
            self._sync_window_to_pixmap()
            self._sync_frames_left -= 1

        # 배경 제거 모드면 마스크 없음(알파 사용). chroma일 때만 흰색 마스크
        if BG_MODE == "chroma":
            pix = self.movie.currentPixmap()
            self.setMask(pix.createMaskFromColor(QtGui.QColor(255,255,255), QtCore.Qt.MaskOutColor))
        else:
            self.clearMask()

    # ---------- 유틸 ----------
    def _clamp_to_screen(self):
        g = self.geometry(); scr = available_geo(self)
        x = max(scr.x(), min(g.x(), scr.x()+scr.width()-self.width()))
        y = max(scr.y(), min(g.y(), scr.y()+scr.height()-self.height()))
        if x != g.x() or y != g.y():
            self.move(x, y)

    def _snap_floor(self):
        scr = available_geo(self)
        bottom_y = scr.y() + scr.height() - self.height() - FLOOR_MARGIN
        self.move(self.x() or scr.x()+40, bottom_y)

    def _enter_climb(self, side: str):
        if side == "left":
            self.set_action("climb_left"); self.follow_resume_dir = 1
        else:
            self.set_action("climb_right"); self.follow_resume_dir = -1
        self.climb_hold_until = time.monotonic() + 10.0
        if hasattr(self, "climb_hold_timer") and self.climb_hold_timer:
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

    # ---------- 메인 루프(기능은 이전 버전과 동일) ----------
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

        # 1) 중력
        in_climb = self.current_action in ("climb_left","climb_right")
        if (not self.stop_move) and (not self.dragging) and (g.y() < bottom) and (not in_climb):
            self.vy += self.ay
            ny = min(bottom, g.y() + int(self.vy))
            self.move(g.x(), ny)
            if ny >= bottom:
                if abs(self.vy) > 3.5:
                    self.vy = -abs(self.vy) * 0.60  # 약간 증가한 탄성
                    self.vx *= 0.9
                else:
                    self.vy = 0.0
                    if self.follow_mouse:
                        pass
                    elif self.random_walk:
                        if self.vx == 0: self.vx = random.choice([-2.0, 2.0])
                        self.set_action("walk_right" if self.vx>0 else "walk_left")
                    else:
                        self.set_action("idle")
            return

        # 2) Climb 유지/작업표시줄 근처면 러닝 복귀(팔로우 시)
        if in_climb:
            if self.follow_mouse:
                mp = QtGui.QCursor.pos()
                if (bottom - g.y()) <= CLIMB_TO_RUN_FLOOR_NEAR:
                    dx = mp.x() - (g.x() + self.width()//2)
                    self.vy = 0.0
                    self.move(g.x(), bottom)
                    self.set_action("run_right" if dx > 0 else "run_left")
                    self.force_run_until = now + 0.8
                    return
                target_y = mp.y() - self.height()//2
                dy = 0
                if abs(g.y() - target_y) > 2:
                    dy = -2 if g.y() > target_y else 2
                ny = max(scr.y(), min(bottom, g.y()+dy))
                self.move(g.x(), ny)
                if (self.current_action == "climb_left"  and g.x() > left_edge + EDGE_MARGIN) or \
                   (self.current_action == "climb_right" and g.x() < right_edge - EDGE_MARGIN):
                    self._end_climb_hold()
                return
            else:
                dy = -1 if (QtCore.QTime.currentTime().msec()//500)%2==0 else 1
                ny = max(scr.y(), min(bottom, g.y()+dy))
                self.move(g.x(), ny)
            if self.climb_hold_until and now >= self.climb_hold_until:
                self._end_climb_hold()
            return

        if self.stop_move or self.dragging:
            return

        # 3) Follow
        if self.follow_mouse:
            mp = QtGui.QCursor.pos()
            cx = g.x() + self.width()//2
            dx = mp.x() - cx
            dist = abs(dx)
            if dist <= 28 or g.contains(mp):
                if self.current_action != "jump":
                    self.set_action("jump")
                return
            if g.x() <= left_edge + EDGE_MARGIN:
                self._enter_climb("left"); return
            if g.x() >= right_edge - EDGE_MARGIN:
                self._enter_climb("right"); return

            if self.follow_resume_deadline > now and self.current_action not in ("climb_left","climb_right","jump"):
                self.set_action("run_right" if self.follow_resume_dir>0 else "run_left")

            speed = 6 if (now < self.force_run_until or dist > 400) else 3
            step = speed if dx>0 else -speed
            nx = g.x() + step
            if abs((nx + self.width()//2) - mp.x()) < speed:
                nx = mp.x() - self.width()//2
            nx = max(left_edge, min(right_edge, nx))
            self.move(nx, g.y())
            if (now < self.force_run_until) or dist > 200:
                self.set_action("run_right" if dx>0 else "run_left")
            else:
                self.set_action("walk_right" if dx>0 else "walk_left")
            self.check_bounce()
            return

        # 4) Random Walk
        if self.random_walk:
            if self.vx == 0:
                self.vx = random.choice([-2.0, 2.0])
            nx = g.x() + int(self.vx)
            if nx <= left_edge:
                nx = left_edge; self.vx = abs(self.vx); self.check_bounce()
            elif nx >= right_edge:
                nx = right_edge; self.vx = -abs(self.vx); self.check_bounce()
            self.move(nx, g.y())
            self.set_action("walk_right" if self.vx>0 else "walk_left")
            return

        # 5) 정지
        self.set_action("idle")

    def check_bounce(self):
        g = self.geometry(); scr = available_geo(self)
        hit_left  = g.x() <= scr.x()
        hit_right = g.x() + self.width() >= (scr.x() + scr.width())
        if hit_left or hit_right:
            self.vx = -self.vx if self.vx != 0 else self.vx
            self.vy = -10
            self.set_action("jump")
            nx = scr.x()+1 if hit_left else scr.x()+scr.width()-self.width()-1
            self.move(nx, g.y())
            QtCore.QTimer.singleShot(600, self._end_bounce)

    def _end_bounce(self):
        if self.follow_mouse:
            self.set_action("run_right" if self.vx>0 else "run_left")
        elif self.random_walk:
            self.set_action("walk_right" if self.vx>0 else "walk_left")
        else:
            self.set_action("idle")

# HiDPI 보정
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
