# -*- coding: utf-8 -*-
import sys, os, random, math, time
from collections import deque
from pathlib import Path
from PyQt5 import QtCore, QtGui, QtWidgets

CHAR_NAME = "Yujeong"
BG_MODE   = "chroma"  # "chroma" or "rembg"
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
        self.vx = 0
        self.vy = 0.0
        self.ay = 1.1  # 중력
        self.dragging = False
        self.drag_offset = QtCore.QPoint(0, 0)

        # 상태
        self.follow_mouse = True
        self.random_walk = False
        self.stop_move = False
        self.always_active = True
        self.sleeping = False

        # 클릭(연속 5회 감지) & 더블클릭
        self.click_times = deque(maxlen=8)
        self.click_window = 0.9  # 초

        # 앰비언트(Idle/Walk/Dance/Jump)
        self.ambient_timer = QtCore.QTimer(self)
        self.ambient_timer.timeout.connect(self.pick_ambient_action)
        self.ambient_timer.start(4000)

        # 메인 루프
        self.tick = QtCore.QTimer(self)
        self.tick.timeout.connect(self.update_loop)
        self.tick.start(16)

        self.make_menu()
        self.set_action("idle")
        self.resize_to_movie()
        self.move(100, 100)

    # ----- 메뉴 -----
    def make_menu(self):
        self.menu = QtWidgets.QMenu(self)
        self.act_follow = self.menu.addAction("마우스 따라가기")
        self.act_random = self.menu.addAction("랜덤 이동")
        self.act_stop   = self.menu.addAction("이동 정지")
        self.menu.addSeparator()
        self.act_dance  = self.menu.addAction("춤추기")
        self.act_eat    = self.menu.addAction("간식주기")
        self.act_pet    = self.menu.addAction("쓰다듬기")
        self.act_ex     = self.menu.addAction("운동하기(무작위)")
        self.act_sleep  = self.menu.addAction("잠자기")
        self.menu.addSeparator()
        self.act_spawn  = self.menu.addAction("펫 추가")
        self.act_close  = self.menu.addAction("이 펫 닫기")
        self.menu.addSeparator()
        self.act_always = self.menu.addAction("항상 활성화")

        for a in [self.act_follow, self.act_random, self.act_stop, self.act_always]:
            a.setCheckable(True)
        self.act_follow.setChecked(True)
        self.act_always.setChecked(True)

    def contextMenuEvent(self, ev):
        action = self.menu.exec_(self.mapToGlobal(ev.pos()))
        # 토글 상태 반영
        self.follow_mouse = self.act_follow.isChecked()
        self.random_walk  = self.act_random.isChecked()
        self.stop_move    = self.act_stop.isChecked()
        self.always_active= self.act_always.isChecked()

        if action == self.act_dance:
            self.play_temp("dance", self.HOLD_MED)
        elif action == self.act_eat:
            self.play_temp("eat", 1600)
        elif action == self.act_pet:
            self.play_temp("pet", 1600)
        elif action == self.act_ex:
            self.play_exercise()
        elif action == self.act_sleep:
            self.start_sleep()
        elif action == self.act_spawn:
            g = self.geometry()
            self.mgr.spawn(pos=QtCore.QPoint(g.x()+40, g.y()+20))
        elif action == self.act_close:
            self.mgr.remove(self)

    # ----- 입력 -----
    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            now = time.monotonic()
            self.click_times.append(now)
            self.check_rapid_clicks()
            self.dragging = True
            self.drag_offset = ev.globalPos() - self.frameGeometry().topLeft()
            self.set_action("hang")

    def mouseMoveEvent(self, ev):
        if self.dragging:
            self.move((ev.globalPos() - self.drag_offset))

    def mouseReleaseEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton and self.dragging:
            self.dragging = False
            x, y, w, h = self.geometry().x(), self.geometry().y(), self.width(), self.height()
            scr = QtWidgets.QApplication.primaryScreen().availableGeometry()
            margin = 10
            if x <= margin:
                self.set_action("climb_left")
            elif x + w >= scr.width() - margin:
                self.set_action("climb_right")
            else:
                self.vy = 0.0

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            self.play_temp("surprise", self.HOLD_SHORT, on_done=self.wake_if_sleeping)

    # ----- 동작 -----
    def play_temp(self, key, hold_ms, on_done=None):
        self.set_action(key)
        QtCore.QTimer.singleShot(hold_ms, lambda: (on_done() if on_done else None, self.set_action("idle")))

    def set_action(self, key):
        path = self.anim_paths.get(key)
        if not path or not os.path.exists(path):
            return
        if self.current_action == key and self.movie:
            return
        self.current_action = key
        self.movie = QtGui.QMovie(path)
        self.label.setMovie(self.movie)
        self.movie.frameChanged.connect(self._on_frame_changed)
        self.movie.start()
        self.resize_to_movie()

    def resize_to_movie(self):
        if self.movie:
            rect = self.movie.frameRect()
            self.setFixedSize(rect.size())

    def _on_frame_changed(self, _i):
        if BG_MODE == "chroma":
            pix = self.movie.currentPixmap()
            mask = pix.createMaskFromColor(QtGui.QColor(255,255,255), QtCore.Qt.MaskOutColor)
            self.setMask(mask)

    def pick_ambient_action(self):
        if not self.always_active or self.dragging or self.stop_move or self.random_walk or self.follow_mouse or self.sleeping:
            return
        r = random.random()
        # idle 55%, walkL 18%, walkR 17%, dance 5%, jump 5%
        if r < 0.55:
            self.set_action("idle")
        elif r < 0.73:
            self.set_action("walk_left")
        elif r < 0.90:
            self.set_action("walk_right")
        elif r < 0.95:
            self.play_temp("dance", 2200)
        else:
            self.jump_action()

    def jump_action(self):
        self.set_action("jump")
        self.vy = -12  # 위로 톡 튀기기
        QtCore.QTimer.singleShot(800, lambda: self.set_action("idle"))

    def play_exercise(self):
        choice = random.choice(["squat","boxing","plank","jumping_jacks"])
        self.play_temp(choice, self.HOLD_LONG)

    def start_sleep(self):
        self.sleeping = True
        self.set_action("sleep")

    def wake_if_sleeping(self):
        if self.sleeping:
            self.sleeping = False

    def check_rapid_clicks(self):
        now = time.monotonic()
        while self.click_times and now - self.click_times[0] > self.click_window:
            self.click_times.popleft()
        if len(self.click_times) >= 5:
            self.click_times.clear()
            self.play_temp("angry", self.HOLD_MED)

    # ----- 메인 루프 -----
    def update_loop(self):
        if self.dragging or self.sleeping:
            return

        geo = self.geometry()
        scr = QtWidgets.QApplication.primaryScreen().availableGeometry()
        bottom = scr.height() - self.height()

        # 낙하
        if geo.y() < bottom and self.current_action not in ("climb_left","climb_right"):
            self.vy += self.ay
            ny = min(bottom, geo.y() + int(self.vy))
            self.move(geo.x(), ny)
            if ny >= bottom:
                self.vy = 0.0
                if self.current_action.startswith("run"):
                    self.set_action("idle")
            return

        # 클라임: 벽 타기
        if self.current_action in ("climb_left","climb_right"):
            dy = -1 if (QtCore.QTime.currentTime().msec()//500)%2==0 else 1
            ny = max(0, min(bottom, geo.y()+dy))
            self.move(geo.x(), ny)
            return

        if self.stop_move:
            return

        # 이동 모드
        if self.follow_mouse:
            mp = QtGui.QCursor.pos()
            cx = geo.x() + self.width()//2
            dx = mp.x() - cx
            dist = abs(dx)
            speed = 6 if dist > 400 else 3
            nx = geo.x() + (speed if dx>0 else -speed)
            nx = max(0, min(scr.width()-self.width(), nx))
            self.move(nx, geo.y())
            # 애니메이션 전환
            if dist > 200:
                self.set_action("run_right" if dx>0 else "run_left")
            elif dist > 20:
                self.set_action("walk_right" if dx>0 else "walk_left")
            else:
                self.set_action("idle")
            # 경계 반사 + 점프
            self.check_bounce()
            return

        if self.random_walk:
            if self.vx == 0:
                self.vx = random.choice([-2, 2])
            nx = geo.x() + self.vx
            nx = max(0, min(scr.width()-self.width(), nx))
            self.move(nx, geo.y())
            self.set_action("walk_right" if self.vx>0 else "walk_left")
            self.check_bounce()
            return

    def check_bounce(self):
        # 좌우 경계 닿으면 방향 반전 + 점프
        geo = self.geometry()
        scr = QtWidgets.QApplication.primaryScreen().availableGeometry()
        hit_left  = geo.x() <= 0
        hit_right = geo.x() + self.width() >= scr.width()
        if hit_left or hit_right:
            self.vx = -self.vx if self.vx != 0 else self.vx
            self.vy = -10
            self.set_action("jump")
            nx = 1 if hit_left else scr.width()-self.width()-1
            self.move(nx, geo.y())
            QtCore.QTimer.singleShot(600, lambda: self.set_action("idle"))

def main():
    app = QtWidgets.QApplication(sys.argv)
    mgr = PetManager(app)
    mgr.spawn()  # 첫 펫
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
