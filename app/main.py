# -*- coding: utf-8 -*-
import sys, os, random, time, math
from collections import deque
from pathlib import Path
from PyQt5 import QtCore, QtGui, QtWidgets

CHAR_NAME = "Yujeong"
BG_MODE   = "rembg"   # "chroma" or "rembg"
SCALE     = 0.40      # 스케일(요청시 숫자만 바꿔서 조절)
BASE_DIR  = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))

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

# ====== 공통 유틸 ======
def work_area():
    # 작업표시줄을 제외한 사용가능 영역
    geo = QtWidgets.QApplication.primaryScreen().availableGeometry()
    return geo

def clamp(n, lo, hi):
    return max(lo, min(hi, n))

# ========== Pet Manager ==========
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
        try:
            self.pets.remove(pet)
        except ValueError:
            pass
        pet.close()
        if not self.pets:
            QtCore.QTimer.singleShot(0, self.app.quit)

# ========== Pet ==========
class Pet(QtWidgets.QMainWindow):
    HOLD_SHORT = 1200
    HOLD_MED   = 2000
    HOLD_LONG  = 3200

    NEAR_JUMP_RADIUS = 36     # 커서 근접 점프 반경(px)
    NEAR_JUMP_COOLDOWN = 0.7  # 근접 점프 쿨다운(s)

    def __init__(self, manager: PetManager):
        super().__init__()
        self.mgr = manager

        self.setWindowTitle(CHAR_NAME)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setWindowFlag(QtCore.Qt.FramelessWindowHint, True)
        self.setWindowFlag(QtCore.Qt.Tool, True)
        self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, True)

        icon_path = (BASE_DIR / "icons" / "icon.ico").as_posix()
        if os.path.exists(icon_path):
            self.setWindowIcon(QtGui.QIcon(icon_path))

        self.label = QtWidgets.QLabel(self)
        self.label.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setCentralWidget(self.label)

        # 경로/상태
        self.anim_paths = {k: (BASE_DIR / "assets" / CHAR_NAME / v).as_posix() for k, v in ACTIONS.items()}
        self.movie = None
        self._movie_buffer = None
        self.gif_bytes = {}  # 메모리 선로딩
        self.current_action = None

        # 물리/상태
        self.vx = 0.0
        self.vy = 0.0
        self.ay = 1.1
        self.dragging = False
        self.drag_offset = QtCore.QPoint(0, 0)
        self.ground_margin = 2

        self.follow_mouse = False
        self.random_walk  = False
        self.always_active = True
        self.stop_move    = False

        self.mode = "normal"   # "normal" | "dance" | "exercise" | "sleep"
        self.exercise_cycle = ["squat","boxing","plank","jumping_jacks"]
        self.exercise_idx = 0
        self.exercise_timer = QtCore.QTimer(self)
        self.exercise_timer.timeout.connect(self._exercise_next)

        self.force_action_until = 0.0
        self.now = time.monotonic

        self.click_times = deque(maxlen=8)
        self.click_window = 0.9

        self.drag_trace = deque(maxlen=6)
        self.ambient_timer = QtCore.QTimer(self)
        self.ambient_timer.timeout.connect(self.pick_ambient_action)
        self.ambient_timer.start(4000)
        self.random_idle_until = 0.0

        self.force_run_until = self.now() + 0.0
        self.cursor_jump_cooldown_until = 0.0

        # 메모리 선로딩(비동기)
        QtCore.QTimer.singleShot(0, self._preload_assets_async)

        # 메인 루프
        self.tick = QtCore.QTimer(self)
        self.tick.timeout.connect(self.update_loop)
        self.tick.start(16)

        self.make_menu()
        self.set_action("idle")
        self.resize_to_movie()
        self._sync_menu_checks()

        # 초기 위치: 작업영역 상단 바깥에서 시작 → 낙하
        wa = work_area()
        start_x = wa.x() + (wa.width() - self.width()) // 2
        start_y = wa.y() - self.height() - 10
        self.move(start_x, start_y)
        self.vy = 0.0

    # ----- 메뉴 -----
    def make_menu(self):
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
        self.menu.addSeparator()
        self.act_always = self.menu.addAction("항상 활성화")

        # 체크 가능 항목들
        for a in [self.act_follow, self.act_random, self.act_always,
                  self.act_dance, self.act_ex, self.act_sleep]:
            a.setCheckable(True)

    def _sync_menu_checks(self):
        # 토글 체크 동기화
        self.act_follow.setChecked(self.follow_mouse)
        self.act_random.setChecked(self.random_walk)
        self.act_always.setChecked(self.always_active)
        self.act_dance.setChecked(self.mode == "dance")
        self.act_ex.setChecked(self.mode == "exercise")
        self.act_sleep.setChecked(self.mode == "sleep")

    def contextMenuEvent(self, ev):
        action = self.menu.exec_(self.mapToGlobal(ev.pos()))
        prev_follow = self.follow_mouse
        prev_random = self.random_walk

        # 기본 토글 반영
        self.follow_mouse = self.act_follow.isChecked()
        self.random_walk  = self.act_random.isChecked()
        self.always_active= self.act_always.isChecked()

        # follow와 random은 동시에 켜지지 않게
        if self.follow_mouse and self.random_walk:
            if action == self.act_follow:
                self.random_walk = False
            elif action == self.act_random:
                self.follow_mouse = False

        # 모드 동작
        if action == self.act_dance:
            if self.mode == "dance":
                self.mode = "normal"; self.set_action("idle")
            else:
                self.mode = "dance"; self.set_action("dance")
        elif action == self.act_eat:
            if self.mode in ("dance","exercise","sleep"):
                self.mode = "normal"
            self.play_temp("eat", 10_000)
        elif action == self.act_pet:
            if self.mode in ("dance","exercise","sleep"):
                self.mode = "normal"
            self.play_temp("pet", 10_000)
        elif action == self.act_ex:
            if self.mode == "exercise":
                self.mode = "normal"; self.exercise_timer.stop(); self.set_action("idle")
            else:
                self.mode = "exercise"
                first = random.choice(self.exercise_cycle)
                self.set_action(first)
                self.exercise_idx = self.exercise_cycle.index(first)
                self.exercise_timer.start(10_000)  # 10초 간격
        elif action == self.act_sleep:
            if self.mode == "sleep":
                self.mode = "normal"; self.set_action("idle")
            else:
                self.mode = "sleep"; self.set_action("sleep")
        elif action == self.act_spawn:
            g = self.geometry()
            self.mgr.spawn(pos=QtCore.QPoint(g.x()+40, g.y()+20))
        elif action == self.act_close:
            self.mgr.remove(self)

        # follow 새로 켜지면 가속 구간 부여
        if (not prev_follow) and self.follow_mouse:
            self.force_run_until = self.now() + 0.8

        self._sync_menu_checks()

    # ----- 메모리 선로딩 -----
    def _preload_assets_async(self):
        for key, path in self.anim_paths.items():
            try:
                with open(path, "rb") as f:
                    self.gif_bytes[key] = f.read()
            except Exception:
                pass
            QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 2)

    # ----- 운동 순환 -----
    def _exercise_next(self):
        if self.mode != "exercise":
            self.exercise_timer.stop()
            return
        self.exercise_idx = (self.exercise_idx + 1) % len(self.exercise_cycle)
        self.set_action(self.exercise_cycle[self.exercise_idx])

    # ----- 입력 처리 -----
    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            now = self.now()
            self.click_times.append(now)
            double = len(self.click_times) >= 2 and (now - self.click_times[-2] <= 0.9)
            if double:
                self.click_times.clear()
                self.play_temp("angry", 5_000)
            else:
                self.play_temp("surprise", 5_000)

            # 드래그 시작
            self.dragging = True
            self.drag_offset = ev.globalPos() - self.frameGeometry().topLeft()
            self.drag_trace.clear()
            self._record_drag_point(ev.globalPos())
            self.set_action("hang")

    def mouseMoveEvent(self, ev):
        if self.dragging:
            self._record_drag_point(ev.globalPos())
            # 드래그 중에도 작업영역 경계 밖으로 안 나가도록 클램프
            wa = work_area()
            pos = ev.globalPos() - self.drag_offset
            nx = clamp(pos.x(), wa.x(), wa.x() + wa.width() - self.width())
            ny = clamp(pos.y(), wa.y(), wa.y() + wa.height() - self.height())
            self.move(nx, ny)

    def mouseReleaseEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton and self.dragging:
            self.dragging = False
            self._apply_throw_velocity()
            # 드래그 해제 시엔 즉시 낙하 시작 (Jump로 바꾸지 않음)
            self.vy = 0.0

    def _record_drag_point(self, gpos: QtCore.QPoint):
        self.drag_trace.append((QtCore.QPoint(gpos), self.now()))

    def _apply_throw_velocity(self):
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

    # ----- 임시 오버라이드 -----
    def play_temp(self, key, hold_ms, on_done=None):
        self.set_action(key)
        self.force_action_until = self.now() + (hold_ms / 1000.0)
        QtCore.QTimer.singleShot(
            hold_ms,
            lambda: (on_done() if on_done else None, self._end_temp())
        )

    def _end_temp(self):
        self.force_action_until = 0.0
        # 토글 모드 유지
        if self.mode == "dance":
            self.set_action("dance")
        elif self.mode == "exercise":
            pass
        elif self.mode == "sleep":
            self.set_action("sleep")
        else:
            if self.follow_mouse:
                # 추적 루프에서 상태 갱신
                pass
            elif self.random_walk:
                self.set_action("walk_right" if self.vx>0 else "walk_left")
            else:
                self.set_action("idle")
        self._sync_menu_checks()

    # ----- QBuffer 기반 set_action (초기 프레임 강제 로드) -----
    def set_action(self, key):
        path = self.anim_paths.get(key)
        if not path:
            return
        if self.current_action == key and self.movie:
            return

        self.current_action = key

        data = self.gif_bytes.get(key)
        if data is not None:
            ba = QtCore.QByteArray(data)
            self._movie_buffer = QtCore.QBuffer(self)
            self._movie_buffer.setData(ba)
            self._movie_buffer.open(QtCore.QIODevice.ReadOnly)
            self.movie = QtGui.QMovie(self._movie_buffer, b"gif", self)
        else:
            self.movie = QtGui.QMovie(path)

        self.movie.setCacheMode(QtGui.QMovie.CacheAll)
        # 논리 캔버스 기준 사이즈 설정
        logical = self.movie.frameRect().size()
        scaled  = QtCore.QSize(max(1, int(logical.width()*SCALE)),
                               max(1, int(logical.height()*SCALE)))
        self.movie.setScaledSize(scaled)
        # 첫 프레임 강제 로드 → 라벨/창 크기 확정, 잘림 방지
        self.movie.jumpToFrame(0)

        self.label.setMovie(self.movie)
        self.movie.frameChanged.connect(self._on_frame_changed)
        self.movie.start()

        self.label.resize(scaled)
        self.setFixedSize(scaled)

        # 현재 위치가 작업영역을 침범하지 않게 한 번 더 정렬
        wa = work_area()
        g = self.geometry()
        nx = clamp(g.x(), wa.x(), wa.x() + wa.width() - g.width())
        ny = clamp(g.y(), wa.y(), wa.y() + wa.height() - g.height())
        if (nx, ny) != (g.x(), g.y()):
            self.move(nx, ny)

    def resize_to_movie(self):
        if self.movie:
            logical = self.movie.frameRect().size()
            scaled  = QtCore.QSize(max(1, int(logical.width()*SCALE)),
                                   max(1, int(logical.height()*SCALE)))
            self.label.resize(scaled)
            self.setFixedSize(scaled)

    def _on_frame_changed(self, _i):
        if BG_MODE == "chroma":
            pix = self.movie.currentPixmap()
            mask = pix.createMaskFromColor(QtGui.QColor(255,255,255), QtCore.Qt.MaskOutColor)
            self.setMask(mask)
        else:
            self.clearMask()

    # ----- 앰비언트 -----
    def pick_ambient_action(self):
        if (not self.follow_mouse) and (not self.random_walk):
            return
        if self.mode in ("dance","exercise","sleep"):
            return
        if self.dragging or self.stop_move:
            return
        if self.now() < self.force_action_until:
            return
        if self.random_walk:
            if self.now() >= self.random_idle_until and random.random() < 0.12:
                self.random_idle_until = self.now() + 60.0
                self.set_action("idle")

    # ----- 메인 루프 -----
    def update_loop(self):
        now = self.now()
        if self.mode in ("dance","exercise","sleep"):
            return
        if self.dragging:
            return

        wa = work_area()
        g  = self.geometry()
        bottom_y = wa.y() + wa.height() - self.height() + self.ground_margin
        left_x   = wa.x()
        right_x  = wa.x() + wa.width() - self.width()

        # 따라가기: 근접 점프(사각형 확장 + 쿨다운)
        if self.follow_mouse and now >= self.cursor_jump_cooldown_until:
            cursor = QtGui.QCursor.pos()
            # 현재 창 사각형을 반경만큼 확장해 '가까움' 정의
            near_rect = g.adjusted(-self.NEAR_JUMP_RADIUS, -self.NEAR_JUMP_RADIUS,
                                   self.NEAR_JUMP_RADIUS,  self.NEAR_JUMP_RADIUS)
            if near_rect.contains(cursor):
                self.cursor_jump_cooldown_until = now + self.NEAR_JUMP_COOLDOWN
                self.jump_action()

        # 중력 낙하
        in_climb = self.current_action in ("climb_left","climb_right")
        if g.y() < bottom_y and not in_climb:
            self.vy += self.ay
            ny = min(bottom_y, g.y() + int(self.vy))
            self.move(g.x(), ny)
            if ny >= bottom_y:
                if abs(self.vy) > 3.5:
                    self.vy = -abs(self.vy) * 0.45
                    self.vx *= 0.9
                else:
                    self.vy = 0.0
                    if self.random_walk:
                        self.set_action("walk_right" if self.vx>0 else "walk_left")
                    elif self.follow_mouse:
                        pass
                    else:
                        self.set_action("idle")
            return

        # 이동 모드
        if self.follow_mouse:
            mp = QtGui.QCursor.pos()
            cx = g.x() + self.width()//2
            dx = mp.x() - cx
            dist = abs(dx)

            speed = 6 if (now < self.force_run_until or dist > 400) else 3
            step  = speed if dx > 0 else -speed
            nx = clamp(g.x() + step, left_x, right_x)
            self.move(nx, g.y())

            # Idle 금지: 근접해도 걷기/뛰기만
            if (now < self.force_run_until) or dist > 200:
                self.set_action("run_right" if dx>0 else "run_left")
            else:
                self.set_action("walk_right" if dx>0 else "walk_left")

            # 경계 점프 반사
            self.check_bounce()
            return

        if self.random_walk:
            if now < self.random_idle_until:
                self.set_action("idle"); return
            if self.vx == 0:
                self.vx = random.choice([-2.0, 2.0])
                self.set_action("walk_right" if self.vx>0 else "walk_left")
            nx = g.x() + int(self.vx)
            if nx <= left_x:
                nx = left_x; self.vx = abs(self.vx); self.check_bounce()
            elif nx >= right_x:
                nx = right_x; self.vx = -abs(self.vx); self.check_bounce()
            self.move(nx, g.y())
            self.set_action("walk_right" if self.vx>0 else "walk_left")
            return

        # 그 외에는 Idle
        self.set_action("idle")

    def check_bounce(self):
        wa = work_area()
        g  = self.geometry()
        hit_left  = g.x() <= wa.x()
        hit_right = g.x() + g.width() >= wa.x() + wa.width()
        if hit_left or hit_right:
            self.vx = -self.vx if self.vx != 0 else self.vx
            self.vy = -10
            self.set_action("jump")
            nx = wa.x()+1 if hit_left else wa.x()+wa.width()-g.width()-1
            self.move(nx, g.y())
            QtCore.QTimer.singleShot(600, self._end_bounce)

    def _end_bounce(self):
        if self.random_walk:
            self.set_action("walk_right" if self.vx>0 else "walk_left")
        elif self.follow_mouse:
            self.set_action("run_right" if self.vx>0 else "run_left")
        else:
            self.set_action("idle")

    def jump_action(self):
        self.set_action("jump")
        if self.vy > -10:
            self.vy = -12
        QtCore.QTimer.singleShot(800, self._end_jump)

    def _end_jump(self):
        if self.random_walk:
            self.set_action("walk_right" if self.vx>0 else "walk_left")
        elif self.follow_mouse:
            self.set_action("run_right" if self.vx>0 else "run_left")
        else:
            self.set_action("idle")

# --- main: HiDPI 설정 ---
def main():
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)

    app = QtWidgets.QApplication(sys.argv)
    mgr = PetManager(app)
    mgr.spawn()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
