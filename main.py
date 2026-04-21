import cv2
import mediapipe as mp
import socket
import time

# ─────────────────────────────────────────────
#  ANSI Terminal Colors
# ─────────────────────────────────────────────
R  = "\033[91m";  G  = "\033[92m";  Y  = "\033[93m"
B  = "\033[94m";  M  = "\033[95m";  C  = "\033[96m"
W  = "\033[97m";  DIM = "\033[2m";  RST = "\033[0m"
BOLD = "\033[1m"

def log(tag, msg, color=W):
    ts = time.strftime("%H:%M:%S")
    print(f"{DIM}[{ts}]{RST} {color}{BOLD}[{tag}]{RST} {msg}")

BANNER = f"""
{C}╔══════════════════════════════════════════════════════╗
║  {W}{BOLD} ███╗   ███╗██╗██████╗ ███╗   ██╗██╗ ██████╗{C}     ║
║  {W}{BOLD} ████╗ ████║██║██╔══██╗████╗  ██║██║██╔════╝{C}     ║
║  {W}{BOLD} ██╔████╔██║██║██║  ██║██╔██╗ ██║██║██║  ███╗{C}    ║
║  {W}{BOLD} ██║╚██╔╝██║██║██║  ██║██║╚██╗██║██║██║   ██║{C}    ║
║  {W}{BOLD} ██║ ╚═╝ ██║██║██████╔╝██║ ╚████║██║╚██████╔╝{C}    ║
║  {W}{BOLD} ╚═╝     ╚═╝╚═╝╚═════╝ ╚═╝  ╚═══╝╚═╝ ╚═════╝{C}    ║
║                                                      ║
║  {G}{BOLD}  AI Gesture Door Access  —  Laptop AI Brain  {C}     ║
║  {DIM}  Gesture • Face • Socket • State Machine        {C}     ║
╚══════════════════════════════════════════════════════╝{RST}
"""

# ─────────────────────────────────────────────
#  Config
# ─────────────────────────────────────────────
SERVER_IP   = '192.168.1.3'   # ← Update with actual Pi IP
SERVER_PORT = 5005


class AIBrain:
    def __init__(self):
        self.mp_hands   = mp.solutions.hands
        self.mp_face    = mp.solutions.face_detection
        self.mp_drawing = mp.solutions.drawing_utils

        self.hands = self.mp_hands.Hands(
            min_detection_confidence=0.7,
            min_tracking_confidence=0.5,
            max_num_hands=1
        )
        self.face_detection = self.mp_face.FaceDetection(
            min_detection_confidence=0.5
        )

        self.sock                  = None
        self.current_state         = "IDLE"
        self.validation_start_time = 0
        self.gesture_start_time    = None
        self.hardware_start_time   = None
        self.TIMEOUT_DURATION      = 5.0
        self.DOOR_OPEN_DURATION    = 30.0
        self.DOOR_WARNING_SECS     = 10.0
        self.last_ping             = 0
        self.dist_val              = -1.0

    # ─── Socket ────────────────────────────────
    def connect_to_server(self):
        log("NET", f"Connecting to {SERVER_IP}:{SERVER_PORT} …", C)
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect((SERVER_IP, SERVER_PORT))
            self.sock = s
            log("NET", f"Connected to Pi Hardware Server!  ✔", G)
            return True
        except Exception as e:
            log("NET", f"Connection failed: {e}", R)
            self.sock = None
            return False

    def send_command(self, cmd):
        if not self.sock:
            return None
        try:
            self.sock.sendall((cmd + '\n').encode('utf-8'))
            return self.sock.recv(1024).decode('utf-8').strip()
        except Exception as e:
            log("NET", f"Socket error: {e}", R)
            self.sock = None
            return None

    # ─── Gesture ───────────────────────────────
    def is_thumb_up(self, hand_landmarks):
        lm = hand_landmarks.landmark
        thumb_extended = lm[4].y < lm[2].y
        index_curled   = lm[8].y > lm[5].y
        middle_curled  = lm[12].y > lm[9].y
        ring_curled    = lm[16].y > lm[13].y
        pinky_curled   = lm[20].y > lm[17].y
        return thumb_extended and index_curled and middle_curled and ring_curled and pinky_curled

    # ─── Main Loop ─────────────────────────────
    def run(self):
        print(BANNER)
        while not self.connect_to_server():
            log("NET", "Waiting for Pi server … retrying in 2s", Y)
            time.sleep(2)

        cap = cv2.VideoCapture(0)
        log("CAM", "Webcam opened.  Press Q to quit.", G)

        while True:
            if not self.sock:
                log("NET", "Connection lost — reconnecting …", Y)
                if not self.connect_to_server():
                    time.sleep(1)
                    continue

            ret, frame = cap.read()
            if not ret:
                break

            frame     = cv2.flip(frame, 1)
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            # ── 1. Distance ping (0.2 s rate-limit) ──
            resp = None
            if time.time() - self.last_ping > 0.2:
                resp           = self.send_command("GET_DIST")
                self.last_ping = time.time()
                if resp and resp != "BUSY":
                    try:
                        self.dist_val = float(resp)
                    except ValueError:
                        pass

            face_detected = False
            gesture       = "None"

            # ── 2. Face Detection — only when in range ──
            if 10.0 <= self.dist_val <= 30.0:
                face_results = self.face_detection.process(rgb_frame)
                if face_results.detections:
                    face_detected = True
                    for det in face_results.detections:
                        self.mp_drawing.draw_detection(frame, det)

                if self.current_state == "IDLE" and face_detected:
                    self.current_state         = "VALIDATING"
                    self.validation_start_time = time.time()
                    self.send_command("STATE:VALIDATING")
                    log("STATE", "HUMAN IN RANGE & FACE DETECTED  →  VALIDATING", G)

                # ── 3. Hand Tracking — only while validating ──
                elif self.current_state == "VALIDATING":
                    hand_results = self.hands.process(rgb_frame)
                    if hand_results.multi_hand_landmarks:
                        for hl in hand_results.multi_hand_landmarks:
                            self.mp_drawing.draw_landmarks(frame, hl, self.mp_hands.HAND_CONNECTIONS)
                            gesture = "THUMB UP" if self.is_thumb_up(hl) else "UNKNOWN HAND"

                    if gesture == "THUMB UP":
                        if self.gesture_start_time is None:
                            self.gesture_start_time = time.time()
                        elif time.time() - self.gesture_start_time >= 1.5:
                            self.send_command("ACTION:OPEN")
                            self.current_state       = "WAITING_ON_HARDWARE"
                            self.hardware_start_time = time.time()
                            self.gesture_start_time  = None
                            log("ACCESS", "GRANTED  ✔  Door opening …", G)
                    else:
                        self.gesture_start_time = None
                        if time.time() - self.validation_start_time > self.TIMEOUT_DURATION:
                            self.send_command("ACTION:DENIED")
                            self.current_state = "IDLE"
                            log("ACCESS", "DENIED  ✘  Gesture timeout — returning to IDLE", R)

            else:
                if self.current_state == "VALIDATING":
                    self.current_state      = "IDLE"
                    self.gesture_start_time = None
                    self.send_command("STATE:IDLE")
                    log("STATE", "Human stepped out of range  →  IDLE", Y)
                elif self.current_state == "WAITING_ON_HARDWARE":
                    if resp != "BUSY":
                        self.current_state       = "IDLE"
                        self.hardware_start_time = None

            # ── Rich Camera Overlay ──
            self._draw_overlay(frame, face_detected, gesture)

            cv2.imshow("MIDNIGHT TECHIE  ·  AI Gesture Access System", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

        cap.release()
        cv2.destroyAllWindows()
        if self.sock:
            self.sock.close()
        log("SYS", "Shutdown complete.", DIM)

    # ─── Overlay ───────────────────────────────
    def _draw_overlay(self, frame, face_detected, gesture):
        h, w = frame.shape[:2]
        now  = time.time()

        def fill_alpha(x1, y1, x2, y2, color, alpha=0.55):
            sub     = frame[y1:y2, x1:x2]
            colored = sub.copy()
            colored[:] = color
            cv2.addWeighted(colored, alpha, sub, 1 - alpha, 0, sub)
            frame[y1:y2, x1:x2] = sub

        def pulse_border(color, speed=1.5, lo=3, hi=10):
            t = int(abs((now * speed % 1.0) - 0.5) * 2 * (hi - lo)) + lo
            cv2.rectangle(frame, (0, 0), (w - 1, h - 1), color, t)

        def big_text(txt, font_scale, thickness, y_center):
            fn = cv2.FONT_HERSHEY_DUPLEX
            (tw, th), _ = cv2.getTextSize(txt, fn, font_scale, thickness)
            return (w // 2 - tw // 2, y_center + th // 2), (tw, th)

        FN   = cv2.FONT_HERSHEY_SIMPLEX
        FND  = cv2.FONT_HERSHEY_DUPLEX

        # ── Status HUD (bottom-left, always visible) ──
        fill_alpha(0, h - 112, 300, h, (8, 8, 8), 0.60)
        st_color = {"IDLE": (80, 200, 80), "VALIDATING": (0, 220, 255),
                    "WAITING_ON_HARDWARE": (0, 180, 100)}.get(self.current_state, (200, 200, 200))
        cv2.putText(frame, f"State:   {self.current_state}", (8, h - 90), FN, 0.50, st_color, 1)
        cv2.putText(frame, f"Dist:    {self.dist_val:.1f} cm",           (8, h - 68), FN, 0.50, (255, 220, 0), 1)
        cv2.putText(frame, f"Face:    {'Detected' if face_detected else 'None'}", (8, h - 46), FN, 0.50, (0, 200, 255) if face_detected else (60, 60, 60), 1)
        cv2.putText(frame, f"Gesture: {gesture}",                        (8, h - 24), FN, 0.50, (0, 255, 60) if gesture == "THUMB UP" else (60, 60, 60), 1)
        cv2.putText(frame, "Press Q to quit", (8, h - 4), FN, 0.35, (50, 50, 50), 1)

        # ── Watermark ──
        cv2.putText(frame, "MIDNIGHT TECHIE", (w - 178, h - 8), FN, 0.42, (40, 40, 40), 1)

        # ── VALIDATING ──
        if self.current_state == "VALIDATING":
            time_left = max(0.0, self.TIMEOUT_DURATION - (now - self.validation_start_time))
            fill_alpha(0, 0, w, 62, (0, 55, 90), 0.68)
            cv2.putText(frame, "SHOW  THUMB UP  TO UNLOCK", (w // 2 - 190, 38), FN, 0.82, (0, 230, 255), 2)

            # Big countdown
            num_str = f"{time_left:.1f}s"
            pos, _ = big_text(num_str, 3.2, 5, h // 2)
            fill_alpha(pos[0] - 18, h // 2 - 80, pos[0] + 200, h // 2 + 36, (0, 25, 45), 0.55)
            cv2.putText(frame, num_str, pos, FND, 3.2, (0, 220, 255) if time_left > 2 else (60, 60, 255), 5)

            # Timeout bar
            bw = int(w * time_left / self.TIMEOUT_DURATION)
            cv2.rectangle(frame, (0, h - 14), (w, h), (15, 15, 15), -1)
            cv2.rectangle(frame, (0, h - 14), (bw, h), (0, 200, 255) if time_left > 2 else (40, 40, 220), -1)

            # Hold bar
            if self.gesture_start_time is not None:
                pct  = min(1.0, (now - self.gesture_start_time) / 1.5)
                hw_  = int(w * pct)
                cv2.rectangle(frame, (0, h - 30), (w, h - 16), (10, 30, 10), -1)
                cv2.rectangle(frame, (0, h - 30), (hw_, h - 16), (0, 255, 60), -1)
                cv2.putText(frame, f"HOLD {int(pct*100)}%", (w//2-42, h-18), FN, 0.46, (0, 255, 70), 1)

            pulse_border((0, 180, 255), speed=1.8, lo=3, hi=9)

        # ── WAITING_ON_HARDWARE (3-phase) ──
        elif self.current_state == "WAITING_ON_HARDWARE":
            elapsed   = now - self.hardware_start_time if self.hardware_start_time else 0
            time_left = max(0.0, self.DOOR_OPEN_DURATION - elapsed)

            if time_left > 10.0:
                # Phase 1 — Green
                fill_alpha(0, 0, w, 62, (0, 70, 0), 0.65)
                cv2.putText(frame, "ACCESS GRANTED  ·  DOOR OPEN", (w//2-198, 36), FND, 0.92, (80, 255, 100), 2)
                cv2.putText(frame, f"Auto-closing in  {int(time_left)}s", (w//2-80, 56), FN, 0.5, (160, 255, 160), 1)
                bw = int(w * time_left / self.DOOR_OPEN_DURATION)
                cv2.rectangle(frame, (0, h-12), (w, h), (8, 30, 8), -1)
                cv2.rectangle(frame, (0, h-12), (bw, h), (0, 210, 60), -1)
                pulse_border((0, 160, 0), speed=0.7, lo=2, hi=6)

            elif time_left > 5.0:
                # Phase 2 — Yellow / Cyan
                flash = int(now * 2) % 2 == 0
                fill_alpha(0, 0, w, 62, (0, 85, 115) if flash else (0, 45, 65), 0.75)
                cv2.putText(frame, f"CLOSING IN  {int(time_left)}s", (w//2-152, 38), FND, 1.05, (0, 220, 255), 2)
                cv2.putText(frame, "Door is closing — GET INSIDE!", (w//2-162, 57), FN, 0.5, (200, 240, 255), 1)
                pos, _ = big_text(str(int(time_left)), 5.5, 8, h//2)
                fill_alpha(pos[0]-22, h//2-110, pos[0]+100, h//2+34, (0, 35, 55), 0.52)
                cv2.putText(frame, str(int(time_left)), pos, FND, 5.5, (0, 220, 255), 8)
                bw = int(w * time_left / self.DOOR_OPEN_DURATION)
                cv2.rectangle(frame, (0, h-12), (w, h), (8, 25, 35), -1)
                cv2.rectangle(frame, (0, h-12), (bw, h), (0, 180, 220), -1)
                pulse_border((0, 200, 255) if flash else (0, 70, 110), speed=2.2, lo=4, hi=11)

            else:
                # Phase 3 — Red urgent
                flash = int(now * 3) % 2 == 0
                fill_alpha(0, 0, w, 62, (0, 0, 140) if flash else (0, 0, 55), 0.82)
                wc = (100, 100, 255) if flash else (255, 80, 80)
                cv2.putText(frame, f"DOOR CLOSING!  {int(time_left)+1}s", (w//2-192, 38), FND, 1.05, wc, 2)
                cv2.putText(frame, "FINAL WARNING — GET INSIDE NOW!", (w//2-196, 57), FN, 0.5, (220, 200, 255), 1)
                pos, _ = big_text(str(int(time_left)+1), 7.0, 10, h//2)
                fill_alpha(pos[0]-22, h//2-135, pos[0]+120, h//2+40, (35, 0, 0), 0.55)
                cv2.putText(frame, str(int(time_left)+1), pos, FND, 7.0, wc, 10)
                bw = int(w * time_left / self.DOOR_OPEN_DURATION)
                cv2.rectangle(frame, (0, h-12), (w, h), (28, 8, 8), -1)
                cv2.rectangle(frame, (0, h-12), (bw, h), (80, 55, 255) if flash else (200, 38, 38), -1)
                pulse_border(wc, speed=4.5, lo=5, hi=15)

        # ── ACCESS DENIED flash ──
        elif self.current_state == "IDLE" and hasattr(self, '_denied_flash_until') and now < self._denied_flash_until:
            fill_alpha(0, 0, w, 65, (0, 0, 120), 0.78)
            cv2.putText(frame, "ACCESS DENIED", (w//2-162, 40), FND, 1.2, (255, 75, 75), 2)
            cv2.putText(frame, "Show Thumb Up to try again", (w//2-152, 60), FN, 0.5, (200, 170, 170), 1)
            pulse_border((0, 0, 200), speed=3.5, lo=4, hi=14)


if __name__ == '__main__':
    brain = AIBrain()
    brain.run()
