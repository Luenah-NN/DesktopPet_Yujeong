# -*- coding: utf-8 -*-
import sys, os, random, time
from collections import deque
from pathlib import Path
from PyQt5 import QtCore, QtGui, QtWidgets

CHAR_NAME = "Yujeong"
BG_MODE   = "rembg"     # "chroma" or "rembg"
SCALE     = 0.50        # 캐릭터 크기 0.6배
BASE_DIR  = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))

# rembg 누락 프레임 대비: 순백 모서리 감지시 1회성 chroma 마스크
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

# 바닥 스냅 대상(작업표시줄 위에 안겹치도록)
FLOOR_SNAP_ACTIONS = {
    "dance","eat","pet","sleep","squat","boxing","plank","jumping_jacks"
}

EDGE_MARGIN   = 10   # 좌우 벽 감지 여유
FLOOR_MARGIN  = 2    # 바닥 위로 조금 띄우기
CLIMB_TO_RUN_FLOOR_NEAR = 20  # 등반중 작업표시줄 근처 전환 임계(픽셀)

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

        # 창/위젯 설정 (초기 잘림 방지)
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
        self.setCentralWidget(self.label)

        # 리소스
        self.anim_paths = {k: (BASE_DIR / "assets" / CHAR_NAME / v).as_posix() for k, v in ACTIONS.items()}
        self.movie = None
        self.current_action = None

        # 물리
        self.vx, self.vy = 0.0, 0.0
        self.ay = 1.1
        self.dragging = False
        self.drag_offset = QtCore.QPoint(0, 0)
        self.press_pos = None
        self.drag_threshold = 6  # px

        # 상태/모드
        self.follow_mouse = False
        self.random_walk  = False
        self.stop_move    = False
        self.always_active = True
        self.mode = "normal"              # "normal" | "dance" | "exercise" | "sleep"
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

        # 팔로우 파라미터/등반
        self.force_run_until = 0.0
        self.near_dist = 28
        self.follow_resume_dir = 0
        self.follow_resume_deadline = 0.0
        self.climb_hold_until = 0.0
        self.climb_hold_timer = None

        # 마스킹 폴백 플래그
        self._use_chroma_mask_this_action = False
        self._mask_checked_this_action = False

        # 메인 루프
        self.tick = QtCore.QTimer(self)
        self.tick.timeout.connect(self.update_loop)
        self.tick.start(16)

        self.make_menu()

        # -------- 초기 액션/워밍업/공중 시작 --------
        self.set_action("idle")
        self._warmup_current_movie(120)
        self.movie.jumpToFrame(0)  # 초기 프레임 강제 로드로 잘림 방지
        self.resize_to_movie()

        # 시작 위치: 화면 상단 근처(공중)에서 낙하, 작업표시줄과 겹치지 않음
        scr = available_geo(self)
        start_x = scr.x() + max(40, scr.width()//2 - self.width()//2)
        start_y = scr.y() + 40
        self.move(start_x, start_y)
        self._clamp_to_screen()  # 경계 보정
        self.vy = 0.0  # 중력으로 자연 낙하

        # 입력/드래그 추적
        self.click_times = deque(maxlen=8)
        self.click_window = 0.9
        self.drag_trace = deque(maxlen=6)

    # ---------- 메뉴 ----------
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

        # 상호배타: follow ↔ random
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
                self.exercise_timer.start(10_000)  # 10초 간격

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

    # ---------- 입력 ----------
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
                # 토글 모드에서는 Hang로 바꾸지 않고 그대로 드래그 (버그 방지)
                self.single_click_timer.stop()
                self.dragging = True
                if self.mode == "normal":
                    self.set_action("hang")
        if self.dragging:
            self._record_drag_point(ev.globalPos())
            self.move(ev.globalPos() - self.drag_offset)
            self._clamp_to_screen()
            # 드래그 중 벽 닿으면 즉시 Climb + 10초 유지
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
            # 토글 모드(춤/운동/잠)면 Hang 고착 방지: 즉시 해당 모션 복원 후 종료
            if self.mode in ("dance","exercise","sleep"):
                # 운동 중이면 현재 동작 유지, 나머지는 모드 액션 고정
                if self.mode == "dance": self.set_action("dance")
                elif self.mode == "sleep": self.set_action("sleep")
                # exercise는 현재 self.current_action 그대로
                return

            # 벽 근접 시 즉시 등반
            if g.x() <= scr.x() + EDGE_MARGIN:
                self._enter_climb("left"); return
            if g.x() >= scr.x() + scr.width() - self.width() - EDGE_MARGIN:
                self._enter_climb("right"); return

            # 그 외엔 낙하 시작(점프 금지, Hang에서 바로 떨어짐)
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
        # follow/random 중엔 Idle 금지
        if self.mode in ("dance","exercise","sleep"):
            return
        if self.follow_mouse or self.random_walk:
            return
        self.set_action("idle")

    # ---------- 액션/리사이즈/마스크 ----------
    def set_action(self, key):
        if key == self.current_action:
            return
        path = self.anim_paths.get(key)
        if not path or not os.path.exists(path):
            return

        self.current_action = key
        self.movie = QtGui.QMovie(path)
        self.movie.setCacheMode(QtGui.QMovie.CacheAll)
        logical = self.movie.frameRect().size()
        scaled  = QtCore.QSize(int(logical.width()*SCALE), int(logical.height()*SCALE))
        if scaled.width() > 0 and scaled.height() > 0:
            self.movie.setScaledSize(scaled)
        self.label.setMovie(self.movie)
        try: self.movie.frameChanged.disconnect()
        except Exception: pass
        self.movie.frameChanged.connect(self._on_frame_changed)
        self.movie.start()
        self.movie.jumpToFrame(0)  # 첫 프레임 강제 로드(잘림 방지)
        self.label.resize(scaled)
        self.setFixedSize(scaled)

        # 바닥 스냅(작업표시줄 위)
        if key in FLOOR_SNAP_ACTIONS:
            self._snap_floor()
            QtCore.QTimer.singleShot(0, self._snap_floor)

        self._clamp_to_screen()
        self._mask_checked_this_action = False
        self._use_chroma_mask_this_action = False
        QtCore.QTimer.singleShot(0, self.resize_to_movie)  # 레이아웃 안정화 후 한 번 더

    def _warmup_current_movie(self, ms):
        t0 = time.monotonic()
        while (time.monotonic() - t0) < (ms/1000.0):
            QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 5)

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
        if BG_MODE == "chroma":
            pix = self.movie.currentPixmap()
            self.setMask(pix.createMaskFromColor(QtGui.QColor(255,255,255), QtCore.Qt.MaskOutColor))
            return
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

    # ---------- 메인 루프 ----------
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
                # 바닥 탄성(약간 증가)
                if abs(self.vy) > 3.5:
                    self.vy = -abs(self.vy) * 0.60
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
                # 작업표시줄 근처에 오면 등반 종료→바닥 스냅→러닝 복귀
                if (bottom - g.y()) <= CLIMB_TO_RUN_FLOOR_NEAR:
                    dx = mp.x() - (g.x() + self.width()//2)
                    # 등반 종료 대신 직접 러닝으로 전환
                    self.vy = 0.0
                    self.move(g.x(), bottom)
                    self.set_action("run_right" if dx > 0 else "run_left")
                    self.force_run_until = now + 0.8
                    return

                # 평소엔 마우스 높이에 맞춰 슬라이드
                target_y = mp.y() - self.height()//2
                dy = 0
                if abs(g.y() - target_y) > 2:
                    dy = -2 if g.y() > target_y else 2
                ny = max(scr.y(), min(bottom, g.y()+dy))
                self.move(g.x(), ny)

                # 벽에서 멀어지면 추락→러닝 재개
                if (self.current_action == "climb_left"  and g.x() > left_edge + EDGE_MARGIN) or \
                   (self.current_action == "climb_right" and g.x() < right_edge - EDGE_MARGIN):
                    self._end_climb_hold()
                return
            else:
                # 팔로우 아님: 가벼운 상하 운동
                dy = -1 if (QtCore.QTime.currentTime().msec()//500)%2==0 else 1
                ny = max(scr.y(), min(bottom, g.y()+dy))
                self.move(g.x(), ny)
            if self.climb_hold_until and now >= self.climb_hold_until:
                self._end_climb_hold()
            return

        if self.stop_move or self.dragging:
            return

        # 3) Follow — 오른쪽 벽 등반 전환 조건 수정 (버그 fix)
        if self.follow_mouse:
            mp = QtGui.QCursor.pos()
            cx = g.x() + self.width()//2
            dx = mp.x() - cx
            dist = abs(dx)

            # 근접 시 Jump 지속
            if dist <= self.near_dist or g.contains(mp):
                if self.current_action != "jump":
                    self.set_action("jump")
                return

            # 좌/우 경계 → climb (우측 전환 조건 수정)
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

            # Idle 금지: run/walk만
            if (now < self.force_run_until) or dist > 200:
                self.set_action("run_right" if dx>0 else "run_left")
            else:
                self.set_action("walk_right" if dx>0 else "walk_left")
            self.check_bounce()
            return

        # 4) Random Walk — 실제 이동
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
    # DPI 라운딩으로 인한 초기 잘림 방지
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
