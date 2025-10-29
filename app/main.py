# -*- coding: utf-8 -*-
import sys, os, random, math, time
from collections import deque
from pathlib import Path
from PyQt5 import QtCore, QtGui, QtWidgets

CHAR_NAME = "Yujeong"
BG_MODE   = "rembg"  # "chroma" or "rembg"
SCALE     = 0.75      # (5) 캐릭터 크기 75%
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

        # 아이콘 (exe에 포함된 icons/icon.ico)
        icon_path = (BASE_DIR / "icons" / "icon.ico").as_posix()
        if os.path.exists(icon_path):
            self.setWindowIcon(QtGui.QIcon(icon_path))

        self.label = QtWidgets.QLabel(self)
        self.label.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setCentralWidget(self.label)

        self.anim_paths = {k: (BASE_DIR / "assets" / CHAR_NAME / v).as_posix() for k, v in ACTIONS.items()}
        self.movie = None
        self.current_action = None

        # 이동/물리
        self.vx = 0.0
        self.vy = 0.0
        self.ay = 1.1  # 중력
        self.dragging = False
        self.drag_offset = QtCore.QPoint(0, 0)
        self.ground_margin = 2  # (7) 바닥에 닿아보이도록 아주 약간 더 내려앉힘

        # 상태
        self.follow_mouse = False   # (1) 기본 OFF
        self.random_walk  = True    # (2) 기본 ON
        self.stop_move    = False
        self.always_active = True
        self.sleeping = False

        # 스테이트/모드
        self.mode = "normal"   # "normal" | "dance" | "exercise" | "sleep"
        self.exercise_cycle = ["squat","boxing","plank","jumping_jacks"]
        self.exercise_idx = 0
        self.exercise_timer = QtCore.QTimer(self)
        self.exercise_timer.timeout.connect(self._exercise_next)

        # 일시적 오버라이드 (ex: 간식/춤/펫/서프라이즈/앵그리 등)
        self.force_action_until = 0.0  # (3) (11) 플레이 잠금 시간
        self.now = time.monotonic

        # 클릭/더블클릭 상태
        self.click_times = deque(maxlen=8)
        self.click_window = 0.9  # 초

        # 던지기(관성) 계산용 드래그 위치 기록
        self.drag_trace = deque(maxlen=6)  # (pos, t)

        # 앰비언트(랜덤 이벤트) 타이머
        self.ambient_timer = QtCore.QTimer(self)
        self.ambient_timer.timeout.connect(self.pick_ambient_action)
        self.ambient_timer.start(4000)

        # 랜덤 이동 중 "가끔 Idle 60초" (#10)
        self.random_idle_until = 0.0

        # 마우스 따라가기 on 전환 감지 (#13)
        self.just_enabled_follow = False
        self.force_run_until = self.now() + 0.0  # follow on 직후 강제 러닝 시간

        # 커서 닿을 때 점프 쿨다운 (#8)
        self.cursor_jump_cooldown_until = 0.0

        # 메인 루프
        self.tick = QtCore.QTimer(self)
        self.tick.timeout.connect(self.update_loop)
        self.tick.start(16)

        self.make_menu()
        self.set_action("idle")
        self.resize_to_movie()
        # 초기 위치
        self.move(100, 100)

    # ----- 메뉴 -----
    def make_menu(self):
        self.menu = QtWidgets.QMenu(self)
        self.act_follow = self.menu.addAction("마우스 따라가기")
        self.act_random = self.menu.addAction("랜덤 이동")
        self.act_stop   = self.menu.addAction("이동 정지")
        self.menu.addSeparator()
        self.act_dance  = self.menu.addAction("춤추기 (토글)")
        self.act_eat    = self.menu.addAction("간식주기")
        self.act_pet    = self.menu.addAction("쓰다듬기")
        self.act_ex     = self.menu.addAction("운동하기(토글)")
        self.act_sleep  = self.menu.addAction("잠자기 (토글)")
        self.menu.addSeparator()
        self.act_spawn  = self.menu.addAction("펫 추가")
        self.act_close  = self.menu.addAction("이 펫 닫기")
        self.menu.addSeparator()
        self.act_always = self.menu.addAction("항상 활성화")

        for a in [self.act_follow, self.act_random, self.act_stop, self.act_always]:
            a.setCheckable(True)

        # 기본값 (1)(2)
        self.act_follow.setChecked(self.follow_mouse)
        self.act_random.setChecked(self.random_walk)
        self.act_always.setChecked(self.always_active)

    def contextMenuEvent(self, ev):
        action = self.menu.exec_(self.mapToGlobal(ev.pos()))
        prev_follow = self.follow_mouse

        # 토글 반영
        self.follow_mouse = self.act_follow.isChecked()
        self.random_walk  = self.act_random.isChecked()
        self.stop_move    = self.act_stop.isChecked()
        self.always_active= self.act_always.isChecked()

        # (13) follow on 직후 강제 러닝 보정
        if (not prev_follow) and self.follow_mouse:
            self.force_run_until = self.now() + 0.8

        # 모드성 액션들
        if action == self.act_dance:
            # (11) 변경 전까지 유지 (토글)
            if self.mode == "dance":
                self.mode = "normal"
                self.set_action("idle")
            else:
                self.mode = "dance"
                self.set_action("dance")
        elif action == self.act_eat:
            self.play_temp("eat", 1600)  # 짧은 임시
        elif action == self.act_pet:
            self.play_temp("pet", 1600)  # 짧은 임시
        elif action == self.act_ex:
            # (12) 운동 토글: 시작 시 랜덤 하나 → 이후 60초씩 순환, 변경 없을 때까지 유지
            if self.mode == "exercise":
                self.mode = "normal"
                self.exercise_timer.stop()
                self.set_action("idle")
            else:
                self.mode = "exercise"
                first = random.choice(self.exercise_cycle)
                self.set_action(first)
                self.exercise_idx = self.exercise_cycle.index(first)
                self.exercise_timer.start(60_000)  # 60초마다 교체
        elif action == self.act_sleep:
            if self.mode == "sleep":
                self.mode = "normal"
                self.set_action("idle")
            else:
                self.mode = "sleep"
                self.set_action("sleep")
        elif action == self.act_spawn:
            g = self.geometry()
            self.mgr.spawn(pos=QtCore.QPoint(g.x()+40, g.y()+20))
        elif action == self.act_close:
            self.mgr.remove(self)

    # ----- 운동 순환 (12) -----
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
            self.check_rapid_clicks()

            self.dragging = True
            self.drag_offset = ev.globalPos() - self.frameGeometry().topLeft()
            self.drag_trace.clear()
            self._record_drag_point(ev.globalPos())
            self.set_action("hang")

    def mouseMoveEvent(self, ev):
        if self.dragging:
            self._record_drag_point(ev.globalPos())
            self.move((ev.globalPos() - self.drag_offset))

    def mouseReleaseEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton and self.dragging:
            self.dragging = False
            self._apply_throw_velocity()

            # 벽 근처면 클라임, 아니면 낙하
            x, y, w, h = self.geometry().x(), self.geometry().y(), self.width(), self.height()
            scr = QtWidgets.QApplication.primaryScreen().availableGeometry()
            margin = 10
            if x <= margin:
                self.set_action("climb_left")
            elif x + w >= scr.width() - margin:
                self.set_action("climb_right")
            else:
                # 낙하 시작
                if self.vy > -8:
                    self.vy = 0.0

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            # (11) sleep일 때도 깨움 + (기본) 서프라이즈
            self.play_temp("surprise", self.HOLD_SHORT, on_done=self.wake_if_sleeping)

    # ----- 드래그 속도 기록/적용 (#9) -----
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
        QtCore.QTimer.singleShot(hold_ms, lambda: (on_done() if on_done else None, self._end_temp()))

    def _end_temp(self):
        self.force_action_until = 0.0
        if self.mode == "dance":
            self.set_action("dance")
        elif self.mode == "exercise":
            pass
        elif self.mode == "sleep":
            self.set_action("sleep")
        else:
            if self.random_walk:
                self.set_action(random.choice(["walk_left","walk_right"]))
            else:
                self.set_action("idle")

    def set_action(self, key):
        path = self.anim_paths.get(key)
        if not path or not os.path.exists(path):
            return
        if self.current_action == key and self.movie:
            return
        self.current_action = key
        self.movie = QtGui.QMovie(path)
        rect = self.movie.frameRect()
        scaled = QtCore.QSize(int(rect.width()*SCALE), int(rect.height()*SCALE))
        if scaled.width() > 0 and scaled.height() > 0:
            self.movie.setScaledSize(scaled)
        self.label.setMovie(self.movie)
        self.movie.frameChanged.connect(self._on_frame_changed)
        self.movie.start()
        self.resize_to_movie()

    def resize_to_movie(self):
        if self.movie:
            rect = self.movie.currentImage().rect() if hasattr(self.movie, "currentImage") else self.movie.frameRect()
            scaled = QtCore.QSize(int(rect.width()*SCALE), int(rect.height()*SCALE))
            if scaled.width() > 0 and scaled.height() > 0:
                self.setFixedSize(scaled)

    def _on_frame_changed(self, _i):
        if BG_MODE == "chroma":
            pix = self.movie.currentPixmap()
            mask = pix.createMaskFromColor(QtGui.QColor(255,255,255), QtCore.Qt.MaskOutColor)
            self.setMask(mask)

    # ----- 앰비언트/랜덤 이벤트 -----
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

    def wake_if_sleeping(self):
        if self.mode == "sleep":
            self.mode = "normal"

    def check_rapid_clicks(self):
        now = self.now()
        while self.click_times and now - self.click_times[0] > 0.9:
            self.click_times.popleft()
        if len(self.click_times) >= 5:
            self.click_times.clear()
            self.play_temp("angry", self.HOLD_MED)

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

        # 커서 닿을 때 점프 (8)
        if self.follow_mouse and now >= self.cursor_jump_cooldown_until:
            cursor = QtGui.QCursor.pos()
            if geo.contains(cursor):
                self.cursor_jump_cooldown_until = now + 1.0
                self.jump_action()

        # 중력 낙하 + 바닥 탄성 (#6, #7, #9)
        in_climb = self.current_action in ("climb_left","climb_right")
        if geo.y() < bottom and not in_climb:
            self.vy += self.ay
            ny = min(bottom, geo.y() + int(self.vy))
            self.move(geo.x(), ny)
            if ny >= bottom:
                if abs(self.vy) > 3.5:
                    self.vy = -abs(self.vy) * 0.40
                    self.vx *= 0.9
                else:
                    self.vy = 0.0
                    if self.random_walk:
                        self.set_action(random.choice(["walk_left","walk_right"]))
                    else:
                        self.set_action("idle")
            return

        # 클라임 중 follow 연동 (#14)
        if in_climb:
            if self.follow_mouse:
                mp = QtGui.QCursor.pos()
                target_y = mp.y() - self.height()//2
                dy = 0
                if abs(geo.y() - target_y) > 2:
                    dy = -2 if geo.y() > target_y else 2
                ny = max(0, min(bottom, geo.y() + dy))
                self.move(geo.x(), ny)
                margin = 14
                if (self.current_action == "climb_left" and geo.x() > margin) or \
                   (self.current_action == "climb_right" and geo.x() < right_edge - margin):
                    self.set_action("hang")
                    self.vy = 0.0
                return
            else:
                dy = -1 if (QtCore.QTime.currentTime().msec()//500)%2==0 else 1
                ny = max(0, min(bottom, geo.y()+dy))
                self.move(geo.x(), ny)
                return

        if now < self.force_action_until:
            return
        if self.stop_move:
            return

        # Follow
        if self.follow_mouse:
            mp = QtGui.QCursor.pos()
            cx = geo.x() + self.width()//2
            dx = mp.x() - cx
            dist = abs(dx)

            margin = 10
            if geo.x() <= margin:
                self.set_action("climb_left"); return
            if geo.x() + self.width() >= scr.width() - margin:
                self.set_action("climb_right"); return

            speed = 6 if (now < self.force_run_until or dist > 400) else 3
            nx = geo.x() + (speed if dx>0 else -speed)
            nx = max(left_edge, min(right_edge, nx))
            self.move(nx, geo.y())

            if (now < self.force_run_until) or dist > 200:
                self.set_action("run_right" if dx>0 else "run_left")
            elif dist > 20:
                self.set_action("walk_right" if dx>0 else "walk_left")
            else:
                self.set_action("idle")

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
            QtCore.QTimer.singleShot(600, lambda: self._end_bounce())

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
        QtCore.QTimer.singleShot(800, lambda: self._end_jump())

    def _end_jump(self):
        if self.random_walk:
            self.set_action("walk_right" if self.vx>0 else "walk_left")
        elif self.follow_mouse:
            self.set_action("run_right" if self.vx>0 else "run_left")
        else:
            self.set_action("idle")

def main():
    app = QtWidgets.QApplication(sys.argv)
    mgr = PetManager(app)
    mgr.spawn()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
