# -*- coding: utf-8 -*-
import sys, os, random, time
from collections import deque
from pathlib import Path
from PyQt5 import QtCore, QtGui, QtWidgets

CHAR_NAME = "Yujeong"
BG_MODE   = "rembg"   # "chroma" or "rembg"
SCALE     = 0.40      # ✔ 20% 추가 축소 (기존 0.50 → 0.40)
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

# ========== Pet Manager (멀티 스폰) ==========
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

# ========== Pet Window ==========
class Pet(QtWidgets.QMainWindow):
    HOLD_SHORT = 1200
    HOLD_MED   = 2000
    HOLD_LONG  = 3200

    def __init__(self, manager: PetManager):
        super().__init__()
        self.mgr = manager

        self.setWindowTitle(CHAR_NAME)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setWindowFlag(QtCore.Qt.FramelessWindowHint, True)
        self.setWindowFlag(QtCore.Qt.Tool, True)
        self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, True)

        # 아이콘
        icon_path = (BASE_DIR / "icons" / "icon.ico").as_posix()
        if os.path.exists(icon_path):
            self.setWindowIcon(QtGui.QIcon(icon_path))

        self.label = QtWidgets.QLabel(self)
        self.label.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setCentralWidget(self.label)

        self.anim_paths = {k: (BASE_DIR / "assets" / CHAR_NAME / v).as_posix() for k, v in ACTIONS.items()}
        self.movie = None
        self._movie_buffer = None      # ✔ QBuffer 유지(메모리 바인딩)
        self.current_action = None

        # 이동/물리
        self.vx = 0.0
        self.vy = 0.0
        self.ay = 1.1
        self.dragging = False
        self.drag_offset = QtCore.QPoint(0, 0)
        self.ground_margin = 2

        # 상태
        self.follow_mouse = False  # 시작은 Idle만
        self.random_walk  = False
        self.stop_move    = False
        self.always_active = True
        self.sleeping     = False

        # 모드/타이머
        self.mode = "normal"   # "normal" | "dance" | "exercise" | "sleep"
        self.exercise_cycle = ["squat","boxing","plank","jumping_jacks"]
        self.exercise_idx = 0
        self.exercise_timer = QtCore.QTimer(self)
        self.exercise_timer.timeout.connect(self._exercise_next)

        # 임시 재생 고정
        self.force_action_until = 0.0
        self.now = time.monotonic

        # 클릭/더블클릭
        self.click_times = deque(maxlen=8)
        self.click_window = 0.9

        # 드래그 관성
        self.drag_trace = deque(maxlen=6)

        # 앰비언트
        self.ambient_timer = QtCore.QTimer(self)
        self.ambient_timer.timeout.connect(self.pick_ambient_action)
        self.ambient_timer.start(4000)
        self.random_idle_until = 0.0

        # follow on 직후 러닝 가속
        self.force_run_until = self.now() + 0.0

        # 커서 닿을 때 점프 쿨다운
        self.cursor_jump_cooldown_until = 0.0

        # ✔ 메모리 선로딩
        self.gif_bytes = {}
        QtCore.QTimer.singleShot(0, self._preload_assets_async)

        # 메인 루프
        self.tick = QtCore.QTimer(self)
        self.tick.timeout.connect(self.update_loop)
        self.tick.start(16)

        self.make_menu()

        # 초기 Idle로 셋업 & 창 크기 확정
        self.set_action("idle")
        self.resize_to_movie()

        # 시작 시 화면 위쪽에서 살짝 떨어뜨려 자연스럽게 낙하
        self.move(120, -self.height())
        self.vy = 0.0

    # ----- 메뉴 -----
    def make_menu(self):
        self.menu = QtWidgets.QMenu(self)
        self.act_follow = self.menu.addAction("마우스 따라가기")
        self.act_random = self.menu.addAction("랜덤 이동")
        # '이동 정지'는 숨김(요청 반영)
        self.menu.addSeparator()
        self.act_dance  = self.menu.addAction("춤추기 (토글)")
        self.act_eat    = self.menu.addAction("간식주기 (10초)")
        self.act_pet    = self.menu.addAction("쓰다듬기 (10초)")
        self.act_ex     = self.menu.addAction("운동하기(토글)")
        self.act_sleep  = self.menu.addAction("잠자기 (토글)")
        self.menu.addSeparator()
        self.act_spawn  = self.menu.addAction("펫 추가")
        self.act_close  = self.menu.addAction("이 펫 닫기")
        self.menu.addSeparator()
        self.act_always = self.menu.addAction("항상 활성화")

        for a in [self.act_follow, self.act_random, self.act_always]:
            a.setCheckable(True)

        # 기본 체크
        self.act_follow.setChecked(self.follow_mouse)
        self.act_random.setChecked(self.random_walk)
        self.act_always.setChecked(self.always_active)

    def contextMenuEvent(self, ev):
        # 메뉴를 여는 동안 모션이 창을 움직여 메뉴가 닫히는 현상 방지
        prev_follow = self.follow_mouse
        prev_random = self.random_walk

        action = self.menu.exec_(self.mapToGlobal(ev.pos()))
        # 토글 동기화
        self.follow_mouse = self.act_follow.isChecked()
        self.random_walk  = self.act_random.isChecked()
        self.always_active= self.act_always.isChecked()

        # 동시에 켜지지 않도록(요청 반영)
        if self.follow_mouse and self.random_walk:
            # 마지막으로 클릭한 항목만 유지
            if action == self.act_follow:
                self.act_random.setChecked(False)
                self.random_walk = False
            elif action == self.act_random:
                self.act_follow.setChecked(False)
                self.follow_mouse = False

        # follow on 직후 러닝 가속
        if (not prev_follow) and self.follow_mouse:
            self.force_run_until = self.now() + 0.8

        # 모드 액션들
        if action == self.act_dance:
            if self.mode == "dance":
                self.mode = "normal"; self.set_action("idle")
            else:
                self.mode = "dance"; self.set_action("dance")
        elif action == self.act_eat:
            # 토글 모드 해제 후 실행(요청 반영)
            if self.mode in ("dance", "exercise", "sleep"):
                self.mode = "normal"
            self.play_temp("eat", 10_000)  # 10초
        elif action == self.act_pet:
            if self.mode in ("dance", "exercise", "sleep"):
                self.mode = "normal"
            self.play_temp("pet", 10_000)  # 10초
        elif action == self.act_ex:
            if self.mode == "exercise":
                self.mode = "normal"; self.exercise_timer.stop(); self.set_action("idle")
            else:
                self.mode = "exercise"
                first = random.choice(self.exercise_cycle)
                self.set_action(first)
                self.exercise_idx = self.exercise_cycle.index(first)
                self.exercise_timer.start(10_000)  # 요청: 10초 간격
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

    # ----- 입력 -----
    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            now = self.now()
            self.click_times.append(now)

            # 클릭 1번 → Surprise(5초), 더블클릭(0.9s내 2회) → Angry(5초)
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
            # 매달림
            self.set_action("hang")

    def mouseMoveEvent(self, ev):
        if self.dragging:
            self._record_drag_point(ev.globalPos())
            self.move((ev.globalPos() - self.drag_offset))

            # 드래그 상태에서 벽 닿으면 즉시 클라임(팔 걸침 연출)
            scr = QtWidgets.QApplication.primaryScreen().availableGeometry()
            x = self.geometry().x()
            if x <= 8:
                self.set_action("climb_left")
            elif x + self.width() >= scr.width() - 8:
                self.set_action("climb_right")

    def mouseReleaseEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton and self.dragging:
            self.dragging = False
            self._apply_throw_velocity()
            # 떨어뜨리기: 점프로 바꾸지 말고 그대로 중력 낙하
            # vy를 0으로 두면 다음 루프에서 ay가 누적되며 자연 낙하
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
        if self.mode == "dance":
            self.set_action("dance")
        elif self.mode == "exercise":
            # 10초마다 순환 중 → 그대로 유지
            pass
        elif self.mode == "sleep":
            self.set_action("sleep")
        else:
            # follow/random 상태에서는 Idle을 강제하지 않음
            if self.follow_mouse:
                # 아무 것도 하지 않음(추적 루프가 상태 갱신)
                pass
            elif self.random_walk:
                self.set_action("walk_right" if self.vx > 0 else "walk_left")
            else:
                self.set_action("idle")

    # ----- QBuffer 기반 set_action (메모리 캐시) -----
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
        logical = self.movie.frameRect().size()
        scaled  = QtCore.QSize(int(logical.width()*SCALE), int(logical.height()*SCALE))
        if scaled.width() > 0 and scaled.height() > 0:
            self.movie.setScaledSize(scaled)

        self.label.setMovie(self.movie)
        self.movie.frameChanged.connect(self._on_frame_changed)
        self.movie.start()

        self.label.resize(scaled)
        self.setFixedSize(scaled)

    def resize_to_movie(self):
        if self.movie:
            logical = self.movie.frameRect().size()
            scaled  = QtCore.QSize(int(logical.width()*SCALE), int(logical.height()*SCALE))
            if scaled.width() > 0 and scaled.height() > 0:
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
        if self.dragging:
            return
        if self.stop_move:
            return
        if self.now() < self.force_action_until:
            return

        if self.random_walk:
            if self.now() >= self.random_idle_until and random.random() < 0.12:
                self.random_idle_until = self.now() + 60.0
                self.set_action("idle")
            return

    # ----- 메인 루프 -----
    def update_loop(self):
        now = self.now()
        if self.mode in ("dance","exercise","sleep"):
            return
        if self.dragging:
            return

        geo = self.geometry()
        scr = QtWidgets.QApplication.primaryScreen().availableGeometry()
        bottom = scr.height() - self.height() + self.ground_margin
        left_edge = 0
        right_edge = scr.width() - self.width()

        # 커서 닿을 때 점프(쿨다운)
        if self.follow_mouse and now >= self.cursor_jump_cooldown_until:
            if geo.contains(QtGui.QCursor.pos()):
                self.cursor_jump_cooldown_until = now + 1.0
                self.jump_action()

        # 중력 낙하 + 바닥 탄성
        in_climb = self.current_action in ("climb_left","climb_right")
        if geo.y() < bottom and not in_climb:
            self.vy += self.ay
            ny = min(bottom, geo.y() + int(self.vy))
            self.move(geo.x(), ny)
            if ny >= bottom:
                if abs(self.vy) > 3.5:
                    self.vy = -abs(self.vy) * 0.45  # 살짝 탄성 ↑
                    self.vx *= 0.9
                else:
                    self.vy = 0.0
                    if self.random_walk:
                        self.set_action("walk_right" if self.vx>0 else "walk_left")
                    elif self.follow_mouse:
                        # 추적 루프가 러닝/워킹 제어
                        pass
                    else:
                        self.set_action("idle")
            return

        # ---- 이동 모드 ----

        # ✔ Follow: 더 이상 Climb 전환 없음 (요청 반영)
        if self.follow_mouse:
            mp = QtGui.QCursor.pos()
            cx = geo.x() + self.width()//2
            dx = mp.x() - cx
            dist = abs(dx)

            speed = 6 if (now < self.force_run_until or dist > 400) else 3
            step  = speed if dx > 0 else -speed
            nx = geo.x() + step
            nx = max(left_edge, min(right_edge, nx))
            self.move(nx, geo.y())

            # Idle 금지: 근접해도 걷기/뛰기만
            if (now < self.force_run_until) or dist > 200:
                self.set_action("run_right" if dx>0 else "run_left")
            else:
                self.set_action("walk_right" if dx>0 else "walk_left")

            # 경계에서 튕기기(점프)
            self.check_bounce()
            return

        # Random Walk
        if self.random_walk:
            if now < self.random_idle_until:
                self.set_action("idle"); return
            if self.vx == 0:
                self.vx = random.choice([-2.0, 2.0])
                self.set_action("walk_right" if self.vx>0 else "walk_left")
            nx = geo.x() + int(self.vx)
            if nx <= left_edge:
                nx = left_edge; self.vx = abs(self.vx); self.check_bounce()
            elif nx >= right_edge:
                nx = right_edge; self.vx = -abs(self.vx); self.check_bounce()
            self.move(nx, geo.y())
            self.set_action("walk_right" if self.vx>0 else "walk_left")
            return

        # 그 외 Idle
        self.set_action("idle")

    def check_bounce(self):
        geo = self.geometry()
        scr = QtWidgets.QApplication.primaryScreen().availableGeometry()
        hit_left  = geo.x() <= 0
        hit_right = geo.x() + self.width() >= (scr.width())
        if hit_left or hit_right:
            self.vx = -self.vx if self.vx != 0 else self.vx
            self.vy = -10
            self.set_action("jump")
            nx = 1 if hit_left else scr.width()-self.width()-1
            self.move(nx, geo.y())
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

# --- main(): HiDPI 플래그 ---
def main():
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)

    app = QtWidgets.QApplication(sys.argv)
    mgr = PetManager(app)
    mgr.spawn()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
