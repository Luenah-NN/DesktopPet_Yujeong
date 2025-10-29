# -*- coding: utf-8 -*-
import sys, os, random, time
from collections import deque
from pathlib import Path
from PyQt5 import QtCore, QtGui, QtWidgets

CHAR_NAME = "Yujeong"
BG_MODE   = "rembg"     # "chroma" or "rembg"
SCALE     = 0.60        # 요구: 0.6배
BASE_DIR  = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))

# rembg 누락 프레임 대비: 순백 배경 폴백 마스킹 (액션당 1회 판정만)
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

# 바닥(작업표시줄 위)으로 스냅할 모션
FLOOR_SNAP_ACTIONS = {
    "idle","dance","eat","pet","sleep","squat","boxing","plank","jumping_jacks"
}

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

        # 창 속성
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

        # 리소스 경로
        self.anim_paths = {k: (BASE_DIR / "assets" / CHAR_NAME / v).as_posix() for k, v in ACTIONS.items()}
        self.movie = None
        self.current_action = None

        # 물리 상태
        self.vx, self.vy = 0.0, 0.0
        self.ay = 1.1
        self.dragging = False
        self.drag_offset = QtCore.QPoint(0, 0)
        self.ground_margin = 2

        # 동작 상태 (요구 5: 시작은 Idle, follow/random/토글 OFF)
        self.follow_mouse = False
        self.random_walk  = False
        self.stop_move    = False
        self.always_active = True
        self.mode = "normal"   # "normal" | "dance" | "exercise" | "sleep"
        self.menu_open = False

        # 운동 순환
        self.exercise_cycle = ["squat","boxing","plank","jumping_jacks"]
        self.exercise_idx = 0
        self.exercise_timer = QtCore.QTimer(self)
        self.exercise_timer.timeout.connect(self._exercise_next)

        # 임시 오버라이드
        self.force_action_until = 0.0
        self.temp_token = 0
        self._temp_stop_saved = {}

        # 클릭 상태
        self.click_times = deque(maxlen=8)
        self.click_window = 0.9

        # 드래그 추적
        self.drag_trace = deque(maxlen=6)

        # 팔로우 파라미터
        self.random_idle_until = 0.0      # 팔로우/랜덤일 땐 Idle 금지 유지
        self.force_run_until = 0.0
        self.near_dist = 28
        self.follow_resume_deadline = 0.0
        self.follow_resume_dir = 0

        # Climb 유지
        self.climb_hold_until = 0.0
        self.climb_hold_timer = None

        # 마스킹 폴백(액션당 1회 판단)
        self._use_chroma_mask_this_action = False
        self._mask_checked_this_action = False

        # 메인 루프
        self.tick = QtCore.QTimer(self)
        self.tick.timeout.connect(self.update_loop)
        self.tick.start(16)

        self.make_menu()
        self.set_action("idle")
        self.resize_to_movie()

        # 시작 위치(작업표시줄 위)
        scr = available_geo(self)
        self.move(scr.x()+40, scr.y() + scr.height() - self.height() - self.ground_margin)
        self._clamp_to_screen()

    # ------------- 메뉴 -------------
    def make_menu(self):
        self.menu = QtWidgets.QMenu(self)
        self.act_follow = self.menu.addAction("마우스 따라가기")
        self.act_random = self.menu.addAction("랜덤 이동")
        self.act_stop   = self.menu.addAction("이동 정지")
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

        # 체크 가능한 항목
        for a in [self.act_follow, self.act_random, self.act_stop, self.act_always,
                  self.act_dance, self.act_ex, self.act_sleep]:
            a.setCheckable(True)

        self._refresh_checks()

    def _refresh_checks(self):
        self.act_follow.setChecked(self.follow_mouse)
        self.act_random.setChecked(self.random_walk)
        self.act_stop.setChecked(self.stop_move)
        self.act_always.setChecked(self.always_active)
        self.act_dance.setChecked(self.mode == "dance")
        self.act_ex.setChecked(self.mode == "exercise")
        self.act_sleep.setChecked(self.mode == "sleep")

    def _exit_modes(self):
        """모든 토글 모드를 해제."""
        if self.mode == "exercise":
            self.exercise_timer.stop()
        self.mode = "normal"
        self._refresh_checks()

    def contextMenuEvent(self, ev):
        # 메뉴 오픈 중엔 움직임을 멈춰 메뉴가 닫히지 않도록
        self.menu_open = True
        saved_follow, saved_random, saved_stop = self.follow_mouse, self.random_walk, self.stop_move
        self.follow_mouse = False
        self.random_walk  = False
        self.stop_move    = True

        action = self.menu.exec_(self.mapToGlobal(ev.pos()))

        # 토글 반영
        self.follow_mouse = self.act_follow.isChecked()
        self.random_walk  = self.act_random.isChecked()
        self.stop_move    = self.act_stop.isChecked()  # 명시적으로 체크된 경우에만 유지
        self.always_active= self.act_always.isChecked()

        # 스페셜 실행 전: 요구 4 — 켜진 토글이 있으면 해제
        if action in (self.act_eat, self.act_pet):
            self._exit_modes()
            self.stop_move = True  # 수행 동안 정지
            if action == self.act_eat:
                self.play_temp("eat", 10_000, stop_during_temp=True)
            else:
                self.play_temp("pet", 10_000, stop_during_temp=True)

        elif action == self.act_dance:
            if self.mode == "dance":
                self._exit_modes()
                # 스페셜 해제 후 팔로우/랜덤이면 Idle 금지
                self._post_special_restore()
            else:
                self._exit_modes()
                self.mode = "dance"
                self.stop_move = True
                self.set_action("dance")

        elif action == self.act_ex:
            if self.mode == "exercise":
                self._exit_modes()
                self._post_special_restore()
            else:
                self._exit_modes()
                self.mode = "exercise"
                self.stop_move = True
                first = random.choice(self.exercise_cycle)
                self.set_action(first)
                self.exercise_idx = self.exercise_cycle.index(first)
                self.exercise_timer.start(10_000)

        elif action == self.act_sleep:
            if self.mode == "sleep":
                self._exit_modes()
                self._post_special_restore()
            else:
                self._exit_modes()
                self.mode = "sleep"
                self.stop_move = True
                self.set_action("sleep")

        elif action == self.act_spawn:
            g = self.geometry()
            self.mgr.spawn(pos=QtCore.QPoint(g.x()+40, g.y()+20))

        elif action == self.act_close:
            self.mgr.remove(self)

        # 팔로우 새로 켜졌다면 초반 가속
        if (not saved_follow) and self.follow_mouse:
            self.force_run_until = time.monotonic() + 0.8

        self._refresh_checks()
        self.menu_open = False

    def _post_special_restore(self):
        """스페셜 종료 후 상태 복귀 (Idle은 follow/random 아닐 때만)."""
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

    # ------------- 입력 -------------
    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            now = time.monotonic()
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
            self.move(ev.globalPos() - self.drag_offset)
            self._clamp_to_screen()
            # 드래그 중 벽 닿으면 즉시 Climb + 10초 유지
            g = self.geometry(); scr = available_geo(self)
            margin = 10
            if g.x() <= scr.x() + margin:
                self._enter_climb("left")
            elif g.x() + self.width() >= scr.x() + scr.width() - margin:
                self._enter_climb("right")

    def mouseReleaseEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton and self.dragging:
            self.dragging = False
            self._apply_throw_velocity()
            # 즉시 낙하: Jump 금지, stop_move 강제 해제
            self.stop_move = False
            g = self.geometry(); scr = available_geo(self); margin = 10
            if g.x() <= scr.x() + margin:
                self._enter_climb("left")
            elif g.x() + self.width() >= scr.x() + scr.width() - margin:
                self._enter_climb("right")
            else:
                self.set_action("hang")
                self.vy = max(self.vy, 2.5)  # 떨어지기 시작

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            self.play_temp("surprise", self.HOLD_SHORT, on_done=self.wake_if_sleeping)

    # ------------- 드래그 속도 -------------
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

    # ------------- 임시 오버라이드 -------------
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
            if self.mode == "exercise":  # 운동은 타이머로 유지
                return
            self.set_action(self.mode if self.mode!="exercise" else self.current_action)
            return
        if self.follow_mouse or self.random_walk:
            return
        self.set_action("idle")

    # ------------- 액션/리사이즈/마스크 -------------
    def set_action(self, key):
        if key == self.current_action:
            return
        path = self.anim_paths.get(key)
        if not path or not os.path.exists(path):
            return

        self.current_action = key
        self.movie = QtGui.QMovie(path)
        self.movie.setCacheMode(QtGui.QMovie.CacheAll)
        # 스케일
        logical = self.movie.frameRect().size()
        scaled  = QtCore.QSize(int(logical.width()*SCALE), int(logical.height()*SCALE))
        if scaled.width() > 0 and scaled.height() > 0:
            self.movie.setScaledSize(scaled)
        # 바인딩
        self.label.setMovie(self.movie)
        try: self.movie.frameChanged.disconnect()
        except Exception: pass
        self.movie.frameChanged.connect(self._on_frame_changed)
        self.movie.start()
        # 크기 고정
        self.label.resize(scaled)
        self.setFixedSize(scaled)

        # 스페셜/Idle 바닥 스냅
        if key in FLOOR_SNAP_ACTIONS:
            scr = available_geo(self)
            bottom_y = scr.y() + scr.height() - self.height() - self.ground_margin
            self.move(self.x(), bottom_y)
        self._clamp_to_screen()

        # 폴백 마스크 판정 초기화(액션당 1회만)
        self._mask_checked_this_action = False
        self._use_chroma_mask_this_action = False

    def resize_to_movie(self):
        if not self.movie: return
        logical = self.movie.frameRect().size()
        scaled  = QtCore.QSize(int(logical.width()*SCALE), int(logical.height()*SCALE))
        if scaled.width() > 0 and scaled.height() > 0:
            self.label.resize(scaled)
            self.setFixedSize(scaled)
            self._clamp_to_screen()

    def _corners_are_pure_white(self, pix: QtGui.QPixmap) -> bool:
        img = pix.toImage().convertToFormat(QtGui.QImage.Format_RGBA8888)
        w, h = img.width(), img.height()
        if w < 2 or h < 2: return False
        for (x,y) in [(0,0),(w-1,0),(0,h-1),(w-1,h-1)]:
            c = img.pixelColor(x,y)
            if not (c.red()==255 and c.green()==255 and c.blue()==255):
                return False
        return True

    def _on_frame_changed(self, _i):
        # 프레임 마다 마스크 재계산은 부담 → 액션당 최초 1회만 판정
        if BG_MODE == "chroma":
            pix = self.movie.currentPixmap()
            mask = pix.createMaskFromColor(QtGui.QColor(255,255,255), QtCore.Qt.MaskOutColor)
            self.setMask(mask)
            return

        # rembg
        self.clearMask()
        if CHROMA_FALLBACK_ON_REMBG and not self._mask_checked_this_action:
            pix = self.movie.currentPixmap()
            if self._corners_are_pure_white(pix):
                self._use_chroma_mask_this_action = True
                self.setMask(pix.createMaskFromColor(QtGui.QColor(255,255,255), QtCore.Qt.MaskOutColor))
            self._mask_checked_this_action = True
        elif self._use_chroma_mask_this_action:
            pix = self.movie.currentPixmap()
            self.setMask(pix.createMaskFromColor(QtGui.QColor(255,255,255), QtCore.Qt.MaskOutColor))

    # ------------- 유틸 -------------
    def _clamp_to_screen(self):
        g = self.geometry(); scr = available_geo(self)
        x = max(scr.x(), min(g.x(), scr.x()+scr.width()-self.width()))
        y = max(scr.y(), min(g.y(), scr.y()+scr.height()-self.height()))
        if x != g.x() or y != g.y():
            self.move(x, y)

    def _enter_climb(self, side: str):
        if side == "left":
            self.set_action("climb_left"); self.follow_resume_dir = 1
        else:
            self.set_action("climb_right"); self.follow_resume_dir = -1
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

    def check_rapid_clicks(self):
        now = time.monotonic()
        while self.click_times and now - self.click_times[0] > self.click_window:
            self.click_times.popleft()
        if len(self.click_times) >= 5:
            self.click_times.clear()
            self.play_temp("angry", self.HOLD_MED)

    # ------------- 메인 루프 -------------
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
        bottom = scr.y() + scr.height() - self.height()  # 바닥 y

        # 1) 중력 우선 처리 (팔로우/랜덤보다 먼저 → 드래그 해제 즉시 낙하 보장)
        in_climb = self.current_action in ("climb_left","climb_right")
        if (not self.stop_move) and (not self.dragging) and (g.y() < bottom) and (not in_climb):
            self.vy += self.ay
            ny = min(bottom, g.y() + int(self.vy))
            self.move(g.x(), ny)
            if ny >= bottom:
                # 탄성(조금 증가)
                if abs(self.vy) > 3.5:
                    self.vy = -abs(self.vy) * 0.60
                    self.vx *= 0.9
                else:
                    self.vy = 0.0
                    if self.follow_mouse:
                        # Idle 금지
                        pass
                    elif self.random_walk:
                        if self.vx == 0: self.vx = random.choice([-2.0, 2.0])
                        self.set_action("walk_right" if self.vx>0 else "walk_left")
                    else:
                        self.set_action("idle")
            return  # 중력 단계에서 프레임 종료 (부드러운 낙하)

        # 2) Climb 유지/종료
        if in_climb:
            if self.follow_mouse:
                mp = QtGui.QCursor.pos()
                target_y = mp.y() - self.height()//2
                dy = 0
                if abs(g.y() - target_y) > 2:
                    dy = -2 if g.y() > target_y else 2
                ny = max(scr.y(), min(bottom, g.y()+dy))
                self.move(g.x(), ny)
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

            # 근접 시 Jump 유지
            if dist <= self.near_dist or g.contains(mp):
                if self.current_action != "jump":
                    self.set_action("jump")
                return

            # 경계 부딪힘 → climb
            margin = 10
            if g.x() <= left_edge + margin:
                self._enter_climb("left"); return
            if g.x() + self.width() >= (right_edge - margin + self.width()*0):
                self._enter_climb("right"); return

            # 팔로우 복귀 러닝 힌트
            if self.follow_resume_deadline > now and self.current_action not in ("climb_left","climb_right","jump"):
                self.set_action("run_right" if self.follow_resume_dir>0 else "run_left")

            speed = 6 if (now < self.force_run_until or dist > 400) else 3
            step = speed if dx>0 else -speed
            nx = g.x() + step
            if abs((nx + self.width()//2) - mp.x()) < speed:
                nx = mp.x() - self.width()//2
            nx = max(left_edge, min(right_edge, nx))
            self.move(nx, g.y())

            # Idle 금지: run/walk만
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
            # Idle 금지
            self.set_action("walk_right" if self.vx>0 else "walk_left")
            return

        # 5) 정지 상태
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

    def wake_if_sleeping(self):
        if self.mode == "sleep":
            self.mode = "normal"
            self._refresh_checks()

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
