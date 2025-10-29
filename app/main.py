# -*- coding: utf-8 -*-
import sys, os, random, math, time
from collections import deque
from pathlib import Path
from PyQt5 import QtCore, QtGui, QtWidgets

CHAR_NAME = "Yujeong"
BG_MODE   = "rembg"   # "chroma" or "rembg"
SCALE     = 0.60      # ← 요구사항: 0.6배
BASE_DIR  = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))

# rembg 프레임 누락 대비: 순백(#FFFFFF)만 보수적으로 자르는 폴백 마스킹
CHROMA_FALLBACK_ON_REMBG = True

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

# 바닥(작업표시줄 위)에 스냅시킬 액션들
FLOOR_SNAP_ACTIONS = {
    "idle","dance","eat","pet","sleep","squat","boxing","plank","jumping_jacks"
}

def current_available_geometry(window: QtWidgets.QWidget) -> QtCore.QRect:
    win = window.windowHandle()
    if win and win.screen():
        return win.screen().availableGeometry()
    p = QtGui.QCursor.pos()
    scr = QtGui.QGuiApplication.screenAt(p)
    if scr:
        return scr.availableGeometry()
    return QtWidgets.QApplication.primaryScreen().availableGeometry()

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

class Pet(QtWidgets.QMainWindow):
    HOLD_SHORT = 1200
    HOLD_MED   = 2000
    HOLD_LONG  = 3200

    def __init__(self, manager: PetManager):
        super().__init__()
        self.mgr = manager

        self.setWindowTitle(CHAR_NAME)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)
        self.setWindowFlag(QtCore.Qt.FramelessWindowHint, True)
        self.setWindowFlag(QtCore.Qt.Tool, True)
        self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, True)

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
        self.ay = 1.1
        self.dragging = False
        self.drag_offset = QtCore.QPoint(0, 0)
        self.ground_margin = 2

        # 상태
        self.follow_mouse = False
        self.random_walk  = True
        self.stop_move    = False
        self.always_active = True
        self.sleeping = False
        self.menu_open = False

        # 모드
        self.mode = "normal"   # "normal" | "dance" | "exercise" | "sleep"
        self.exercise_cycle = ["squat","boxing","plank","jumping_jacks"]
        self.exercise_idx = 0
        self.exercise_timer = QtCore.QTimer(self)
        self.exercise_timer.timeout.connect(self._exercise_next)

        # 임시 오버라이드
        self.force_action_until = 0.0
        self.temp_token = 0
        self._temp_stop_saved = {}

        self._saved_stop_for_dance = None
        self._saved_stop_for_ex = None
        self._saved_stop_for_sleep = None

        # 클릭
        self.click_times = deque(maxlen=8)
        self.click_window = 0.9

        # 던지기
        self.drag_trace = deque(maxlen=6)

        # 앰비언트
        self.ambient_timer = QtCore.QTimer(self)
        self.ambient_timer.timeout.connect(self.pick_ambient_action)
        self.ambient_timer.start(4000)

        # 팔로우 관련
        self.random_idle_until = 0.0
        self.force_run_until = 0.0
        self.near_dist = 28
        self.follow_resume_deadline = 0.0
        self.follow_resume_dir = 0

        # Climb 유지
        self.climb_hold_until = 0.0
        self.climb_hold_timer = None

        # 메인 루프
        self.tick = QtCore.QTimer(self)
        self.tick.timeout.connect(self.update_loop)
        self.tick.start(16)

        self.make_menu()
        self.set_action("idle")
        self.resize_to_movie()

        # 시작 위치: 작업표시줄 제외 하단
        scr = current_available_geometry(self)
        start_x = scr.x() + 40
        start_y = scr.y() + scr.height() - self.height() - self.ground_margin
        self.move(start_x, start_y)
        self._clamp_to_screen()

    # ---------- 메뉴 ----------
    def make_menu(self):
        self.menu = QtWidgets.QMenu(self)
        self.act_follow = self.menu.addAction("마우스 따라가기")
        self.act_random = self.menu.addAction("랜덤 이동")
        self.act_stop   = self.menu.addAction("이동 정지")
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

        for a in [self.act_follow, self.act_random, self.act_stop, self.act_always,
                  self.act_dance, self.act_ex, self.act_sleep]:
            a.setCheckable(True)

        self.act_follow.setChecked(self.follow_mouse)
        self.act_random.setChecked(self.random_walk)
        self.act_always.setChecked(self.always_active)
        self._update_mode_checks()

    def _update_mode_checks(self):
        self.act_dance.setChecked(self.mode == "dance")
        self.act_ex.setChecked(self.mode == "exercise")
        self.act_sleep.setChecked(self.mode == "sleep")

    def contextMenuEvent(self, ev):
        # 메뉴 동안 동작 정지(닫힘 방지)
        self.menu_open = True
        saved_follow = self.follow_mouse
        saved_random = self.random_walk
        saved_stop   = self.stop_move
        self.follow_mouse = False
        self.random_walk  = False
        self.stop_move    = True

        action = self.menu.exec_(self.mapToGlobal(ev.pos()))

        # 토글 반영
        self.follow_mouse = self.act_follow.isChecked()
        self.random_walk  = self.act_random.isChecked()
        self.stop_move    = self.act_stop.isChecked() or self.stop_move
        self.always_active= self.act_always.isChecked()

        if (not saved_follow) and self.follow_mouse:
            self.force_run_until = time.monotonic() + 0.8

        # 스페셜 실행 시 자동 이동 정지
        if action == self.act_dance:
            if self.mode == "dance":
                self.mode = "normal"
                if self._saved_stop_for_dance is not None:
                    self.stop_move = self._saved_stop_for_dance; self._saved_stop_for_dance = None
                self._post_special_restore()
            else:
                self._saved_stop_for_dance = self.stop_move
                self.stop_move = True
                self.mode = "dance"
                self.set_action("dance")
        elif action == self.act_eat:
            self.play_temp("eat", 10_000, stop_during_temp=True)
        elif action == self.act_pet:
            self.play_temp("pet", 10_000, stop_during_temp=True)
        elif action == self.act_ex:
            if self.mode == "exercise":
                self.mode = "normal"
                if self._saved_stop_for_ex is not None:
                    self.stop_move = self._saved_stop_for_ex; self._saved_stop_for_ex = None
                self.exercise_timer.stop()
                self._post_special_restore()
            else:
                self._saved_stop_for_ex = self.stop_move
                self.stop_move = True
                self.mode = "exercise"
                first = random.choice(self.exercise_cycle)
                self.set_action(first)
                self.exercise_idx = self.exercise_cycle.index(first)
                self.exercise_timer.start(10_000)  # 10초 간격 유지
        elif action == self.act_sleep:
            if self.mode == "sleep":
                self.mode = "normal"
                if self._saved_stop_for_sleep is not None:
                    self.stop_move = self._saved_stop_for_sleep; self._saved_stop_for_sleep = None
                self._post_special_restore()
            else:
                self._saved_stop_for_sleep = self.stop_move
                self.stop_move = True
                self.mode = "sleep"
                self.set_action("sleep")
        elif action == self.act_spawn:
            g = self.geometry()
            self.mgr.spawn(pos=QtCore.QPoint(g.x()+40, g.y()+20))
        elif action == self.act_close:
            self.mgr.remove(self)

        self._update_mode_checks()
        if self.mode == "normal":
            if not self.stop_move:
                self.stop_move = saved_stop
        self.menu_open = False

    def _post_special_restore(self):
        # 팔로우/랜덤 이동이면 Idle 금지
        if self.follow_mouse:
            self.set_action("run_right")
        elif self.random_walk:
            if self.vx == 0: self.vx = random.choice([-2.0, 2.0])
            self.set_action("walk_right" if self.vx>0 else "walk_left")
        else:
            self.set_action("idle")

    def _exercise_next(self):
        if self.mode != "exercise":
            self.exercise_timer.stop(); return
        self.exercise_idx = (self.exercise_idx + 1) % len(self.exercise_cycle)
        self.set_action(self.exercise_cycle[self.exercise_idx])

    # ---------- 입력 ----------
    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            now = time.monotonic()
            self.click_times.append(now)
            self.check_rapid_clicks()

            self.dragging = True
            self.drag_offset = ev.globalPos() - self.frameGeometry().topLeft()
            self.drag_trace.clear()
            self._record_drag_point(ev.globalPos())
            self.set_action("hang")   # 드래그 중 hang

    def mouseMoveEvent(self, ev):
        if self.dragging:
            self._record_drag_point(ev.globalPos())
            self.move(ev.globalPos() - self.drag_offset)
            self._clamp_to_screen()
            # 드래그 중 가장자리에 닿으면 즉시 Climb + 10초 유지
            g = self.geometry()
            scr = current_available_geometry(self)
            margin = 10
            if g.x() <= scr.x() + margin:
                self._enter_climb("left")
            elif g.x() + self.width() >= scr.x() + scr.width() - margin:
                self._enter_climb("right")

    def mouseReleaseEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton and self.dragging:
            self.dragging = False
            self._apply_throw_velocity()
            # hang 잔류 방지 토큰 종료 및 즉시 낙하 시작
            self.force_action_until = 0.0
            # 벽 근처면 climb, 아니면 '낙하' 연출: hang 유지 + 중력만 적용 (Jump 금지)
            g = self.geometry()
            scr = current_available_geometry(self)
            margin = 10
            if g.x() <= scr.x() + margin:
                self._enter_climb("left")
            elif g.x() + self.width() >= scr.x() + scr.width() - margin:
                self._enter_climb("right")
            else:
                # 바로 떨어지도록 약간의 하강 속도 부여
                self.set_action("hang")
                self.vy = max(self.vy, 2.5)

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            self.play_temp("surprise", self.HOLD_SHORT, on_done=self.wake_if_sleeping)

    # ---------- 던지기 ----------
    def _record_drag_point(self, gpos: QtCore.QPoint):
        self.drag_trace.append((QtCore.QPoint(gpos), time.monotonic()))

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
            if on_done:
                on_done()
            self._end_temp(token)
        QtCore.QTimer.singleShot(hold_ms, _end)

    def _end_temp(self, token):
        if token != self.temp_token:
            return
        self.force_action_until = 0.0
        if token in self._temp_stop_saved:
            self.stop_move = self._temp_stop_saved.pop(token)

        if self.mode == "dance":
            self.set_action("dance"); return
        if self.mode == "exercise":
            return
        if self.mode == "sleep":
            self.set_action("sleep"); return

        if self.follow_mouse or self.random_walk:
            return
        self.set_action("idle")

    # ---------- 액션/사이즈 ----------
    def set_action(self, key):
        path = self.anim_paths.get(key)
        if not path or not os.path.exists(path):
            return
        if self.current_action == key and self.movie:
            return

        self.current_action = key
        self.movie = QtGui.QMovie(path)
        self.movie.setCacheMode(QtGui.QMovie.CacheAll)

        logical = self.movie.frameRect().size()
        scaled  = QtCore.QSize(int(logical.width()*SCALE), int(logical.height()*SCALE))
        if scaled.width() > 0 and scaled.height() > 0:
            self.movie.setScaledSize(scaled)

        self.label.setMovie(self.movie)
        try:
            self.movie.frameChanged.disconnect()
        except Exception:
            pass
        self.movie.frameChanged.connect(self._on_frame_changed)
        self.movie.start()

        self.label.resize(scaled)
        self.setFixedSize(scaled)

        # 스페셜/Idle 계열은 바닥 스냅 (작업표시줄 위)
        if key in FLOOR_SNAP_ACTIONS:
            scr = current_available_geometry(self)
            bottom_y = scr.y() + scr.height() - self.height() - self.ground_margin
            self.move(self.x(), bottom_y)

        self._clamp_to_screen()

    def resize_to_movie(self):
        if self.movie:
            logical = self.movie.frameRect().size()
            scaled  = QtCore.QSize(int(logical.width()*SCALE), int(logical.height()*SCALE))
            if scaled.width() > 0 and scaled.height() > 0:
                self.label.resize(scaled)
                self.setFixedSize(scaled)
                self._clamp_to_screen()

    # rembg 폴백: 순백 배경만 잘라내기
    def _corners_are_pure_white(self, pix: QtGui.QPixmap) -> bool:
        img = pix.toImage().convertToFormat(QtGui.QImage.Format_RGBA8888)
        w, h = img.width(), img.height()
        if w < 2 or h < 2:
            return False
        pts = [(0,0), (w-1,0), (0,h-1), (w-1,h-1)]
        for (x,y) in pts:
            c = img.pixelColor(x,y)
            if not (c.red()==255 and c.green()==255 and c.blue()==255):
                return False
        return True

    def _on_frame_changed(self, _i):
        if BG_MODE == "chroma":
            pix = self.movie.currentPixmap()
            mask = pix.createMaskFromColor(QtGui.QColor(255,255,255), QtCore.Qt.MaskOutColor)
            self.setMask(mask)
        else:
            self.clearMask()
            if CHROMA_FALLBACK_ON_REMBG:
                pix = self.movie.currentPixmap()
                if self._corners_are_pure_white(pix):
                    mask = pix.createMaskFromColor(QtGui.QColor(255,255,255), QtCore.Qt.MaskOutColor)
                    self.setMask(mask)

    # ---------- climb 10초 유지 → 낙하 ----------
    def _enter_climb(self, side: str):
        if side == "left":
            self.set_action("climb_left"); self.follow_resume_dir = 1
        else:
            self.set_action("climb_right"); self.follow_resume_dir = -1
        self.climb_hold_until = time.monotonic() + 10.0
        if self.climb_hold_timer is not None:
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

    # ---------- 앰비언트 ----------
    def pick_ambient_action(self):
        if self.follow_mouse or self.random_walk:
            return
        if self.mode in ("dance","exercise","sleep"):
            return
        if self.dragging or self.stop_move or self.menu_open:
            return
        if self.now() < self.force_action_until:
            return
        return

    def wake_if_sleeping(self):
        if self.mode == "sleep":
            self.mode = "normal"
            self._update_mode_checks()

    def check_rapid_clicks(self):
        now = time.monotonic()
        while self.click_times and now - self.click_times[0] > self.click_window:
            self.click_times.popleft()
        if len(self.click_times) >= 5:
            self.click_times.clear()
            self.play_temp("angry", self.HOLD_MED)

    # ---------- 메인 루프 ----------
    def update_loop(self):
        now = time.monotonic()

        if self.menu_open:
            return
        if self.mode in ("dance","exercise","sleep"):
            return
        if self.dragging or self.stop_move:
            return

        g = self.geometry()
        scr = current_available_geometry(self)
        bottom = scr.y() + scr.height() - self.height() + self.ground_margin
        left_edge = scr.x()
        right_edge = scr.x() + scr.width() - self.width()

        # Follow
        if self.follow_mouse:
            mp = QtGui.QCursor.pos()
            cx = g.x() + self.width()//2
            dx = mp.x() - cx
            dist = abs(dx)

            if dist <= self.near_dist or g.contains(mp):
                if self.current_action != "jump":
                    self.set_action("jump")
                return

            margin = 10
            if g.x() <= left_edge + margin:
                self._enter_climb("left"); return
            if g.x() + self.width() >= right_edge - margin + self.width():
                self._enter_climb("right"); return

            if self.follow_resume_deadline > now and self.current_action not in ("climb_left","climb_right","jump"):
                if self.follow_resume_dir > 0:
                    self.set_action("run_right")
                elif self.follow_resume_dir < 0:
                    self.set_action("run_left")

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

        # 중력 낙하 + 탄성
        in_climb = self.current_action in ("climb_left","climb_right")
        if g.y() < bottom and not in_climb:
            self.vy += self.ay
            ny = min(bottom, g.y() + int(self.vy))
            self.move(g.x(), ny)
            if ny >= bottom:
                # 탄성 0.60
                if abs(self.vy) > 3.5:
                    self.vy = -abs(self.vy) * 0.60
                    self.vx *= 0.9
                else:
                    self.vy = 0.0
                    if self.random_walk and not self.follow_mouse:
                        if self.vx == 0:
                            self.vx = random.choice([-2.0, 2.0])
                        self.set_action("walk_right" if self.vx>0 else "walk_left")
                    elif not self.follow_mouse and not self.random_walk:
                        self.set_action("idle")
            return

        # climb 중(팔로우 없음): 상하
        if in_climb:
            dy = -1 if (QtCore.QTime.currentTime().msec()//500)%2==0 else 1
            ny = max(scr.y(), min(bottom, g.y()+dy))
            self.move(g.x(), ny)
            if self.climb_hold_until and now >= self.climb_hold_until:
                self._end_climb_hold()
            return

        if now < self.force_action_until:
            return

        # Random Walk
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

        self.set_action("idle")

    def _clamp_to_screen(self):
        g = self.geometry()
        scr = current_available_geometry(self)
        x = max(scr.x(), min(g.x(), scr.x() + scr.width() - self.width()))
        y = max(scr.y(), min(g.y(), scr.y() + scr.height() - self.height()))
        if x != g.x() or y != g.y():
            self.move(x, y)

    def check_bounce(self):
        g = self.geometry()
        scr = current_available_geometry(self)
        hit_left  = g.x() <= scr.x()
        hit_right = g.x() + self.width() >= (scr.x() + scr.width())
        if hit_left or hit_right:
            self.vx = -self.vx if self.vx != 0 else self.vx
            self.vy = -10
            self.set_action("jump")
            nx = scr.x()+1 if hit_left else scr.x() + scr.width() - self.width() - 1
            self.move(nx, g.y())
            QtCore.QTimer.singleShot(600, self._end_bounce)

    def _end_bounce(self):
        if self.random_walk and not self.follow_mouse:
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
        if self.follow_mouse:
            g = self.geometry()
            mp = QtGui.QCursor.pos()
            dist = abs((g.x() + self.width()//2) - mp.x())
            if dist <= self.near_dist or g.contains(mp):
                self.set_action("jump")
                return
            dx = mp.x() - (g.x() + self.width()//2)
            self.set_action("run_right" if abs(dx)>200 and dx>0 else
                            "run_left"  if abs(dx)>200 and dx<0 else
                            "walk_right" if dx>0 else "walk_left")
            return

        if self.random_walk:
            self.set_action("walk_right" if self.vx>0 else "walk_left")
        else:
            self.set_action("idle")

# HiDPI 보정
def main():
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)
    app = QtWidgets.QApplication(sys.argv)
    mgr = PetManager(app)
    mgr.spawn()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
