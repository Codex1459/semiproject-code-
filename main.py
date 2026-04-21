import cv2
import mediapipe as mp
import socket
import time

# ---------------------------------------------
#  ANSI Terminal Colors
# ---------------------------------------------
R  = "\033[91m";  G  = "\033[92m";  Y  = "\033[93m"
B  = "\033[94m";  M  = "\033[95m";  C  = "\033[96m"
W  = "\033[97m";  DIM = "\033[2m";  RST = "\033[0m"
BOLD = "\033[1m"

BANNER = f"""
{C}+------------------------------------------------------+
|  {W}{BOLD} ███╗   ███╗██╗██████╗ ███╗   ██╗██╗ ██████╗{C}     |
|  {W}{BOLD} ████╗ ████║██║██╔══██╗████╗  ██║██║██╔════╝{C}     |
|  {W}{BOLD} ██╔████╔██║██║██║  ██║██╔██╗ ██║██║██║  ███╗{C}    |
|  {W}{BOLD} ██║╚██╔╝██║██║██║  ██║██║╚██╗██║██║██║   ██║{C}    |
|  {W}{BOLD} ██║ ╚═╝ ██║██║██████╔╝██║ ╚████║██║╚██████╔╝{C}    |
|  {W}{BOLD} ╚═╝     ╚═╝╚═╝╚═════╝ ╚═╝  ╚═══╝╚═╝ ╚═════╝{C}    |
|                                                      |
|  {G}{BOLD}  AI Gesture Door Access  --  Laptop AI Brain  {C}     |
|  {DIM}  Gesture | Face | Socket | State Machine        {C}     |
+------------------------------------------------------+{RST}
"""

# ---------------------------------------------
#  Config
# ---------------------------------------------
SERVER_IP   = '192.168.1.3'   # Update with actual Pi IP
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
        self._denied_flash_until   = 0

    def log(self, tag, msg, color=W):
        ts = time.strftime("%H:%M:%S")
        print(f"{DIM}[{ts}]{RST} {color}{BOLD}[{tag}]{RST} {msg}")

    # --- Socket ---
    def connect_to_server(self):
        self.log("NET", f"Connecting to {SERVER_IP}:{SERVER_PORT}...", C)
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect((SERVER_IP, SERVER_PORT))
            self.sock = s
            self.log("NET", "Connected to Pi Hardware Server! [OK]", G)
            return True
        except Exception as e:
            self.log("NET", f"Connection failed: {e}", R)
            self.sock = None
            return False

    def send_command(self, cmd):
        if not self.sock:
            return None
        try:
            self.sock.sendall((cmd + '\n').encode('utf-8'))
            return self.sock.recv(1024).decode('utf-8').strip()
        except Exception as e:
            self.log("NET", f"Socket error: {e}", R)
            self.sock = None
            return None

    # --- Gesture ---
    def is_fist_closed(self, hand_landmarks):
        """ Checks if all fingers are curled (Closed Fist) """
        lm = hand_landmarks.landmark
        
        # Fingers: Tip must be BELOW the PIP joint (curled)
        fingers = [
            lm[8].y > lm[6].y,   # Index
            lm[12].y > lm[10].y, # Middle
            lm[16].y > lm[16-2].y, # Ring (using -2 for knuckle reference)
            lm[20].y > lm[18].y  # Pinky
        ]
        
        # Thumb: Tip must be closer to the palm center than the IP joint
        thumb_curled = lm[4].x > lm[3].x if lm[5].x > lm[17].x else lm[4].x < lm[3].x
        fingers.append(thumb_curled)
        
        # If all fingers are curled, it's a fist
        return sum(fingers) == 5

        # If all fingers are curled, it's a fist
        return sum(fingers) == 5

    # --- Main Loop ---
    def run(self):
        print(BANNER)
        while not self.connect_to_server():
            self.log("NET", "Waiting for Pi server... retrying in 2s", Y)
            time.sleep(2)

        cap = cv2.VideoCapture(0)
        self.log("CAM", "Webcam opened. Press Q to quit.", G)

        while True:
            if not self.sock:
                self.log("NET", "Connection lost -- reconnecting...", Y)
                if not self.connect_to_server():
                    time.sleep(1)
                    continue

            ret, frame = cap.read()
            if not ret: break

            frame     = cv2.flip(frame, 1)
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            # 1. Distance ping
            resp = None
            if time.time() - self.last_ping > 0.2:
                resp = self.send_command("GET_DIST")
                self.last_ping = time.time()
                if resp and resp != "BUSY":
                    try: self.dist_val = float(resp)
                    except ValueError: pass

            face_detected = False
            gesture       = "None"

            # 2. AI Processing
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
                    self.log("STATE", "HUMAN IN RANGE & FACE DETECTED -> VALIDATING", G)

                # Hand Tracking runs always if VALIDATING
                if self.current_state == "VALIDATING":
                    hand_results = self.hands.process(rgb_frame)
                    if hand_results.multi_hand_landmarks:
                        for hl in hand_results.multi_hand_landmarks:
                            self.mp_drawing.draw_landmarks(frame, hl, self.mp_hands.HAND_CONNECTIONS)
                            gesture = "CLOSED FIST" if self.is_fist_closed(hl) else "HAND DETECTED"

                    if gesture == "CLOSED FIST":
                        if self.gesture_start_time is None:
                            self.gesture_start_time = time.time()
                        elif time.time() - self.gesture_start_time >= 1.5:
                            self.send_command("ACTION:OPEN")
                            self.current_state       = "WAITING_ON_HARDWARE"
                            self.hardware_start_time = time.time()
                            self.gesture_start_time  = None
                            self.log("ACCESS", "GRANTED [OK] Fist detected!", G)
                    else:
                        self.gesture_start_time = None
                        if time.time() - self.validation_start_time > self.TIMEOUT_DURATION:
                            self.send_command("ACTION:DENIED")
                            self.current_state = "IDLE"
                            self._denied_flash_until = time.time() + 3.0
                            self.log("ACCESS", "DENIED [X] Gesture timeout - IDLE", R)
            else:
                if self.current_state == "VALIDATING":
                    self.current_state = "IDLE"
                    self.gesture_start_time = None
                    self.send_command("STATE:IDLE")
                    self.log("STATE", "Human stepped out of range -> IDLE", Y)
                elif self.current_state == "WAITING_ON_HARDWARE":
                    if resp and resp != "BUSY":
                        self.current_state = "IDLE"
                        self.hardware_start_time = None

            self._draw_overlay(frame, face_detected, gesture)
            cv2.imshow("AI Gesture Access System", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'): break

        cap.release()
        cv2.destroyAllWindows()
        if self.sock: self.sock.close()

    def _draw_overlay(self, frame, face_detected, gesture):
        h, w = frame.shape[:2]
        now  = time.time()

        def fill_alpha(x1, y1, x2, y2, color, alpha=0.55):
            sub = frame[y1:y2, x1:x2]
            colored = sub.copy(); colored[:] = color
            cv2.addWeighted(colored, alpha, sub, 1 - alpha, 0, sub)
            frame[y1:y2, x1:x2] = sub

        def pulse_border(color, speed=1.5, lo=3, hi=10):
            t = int(abs((now * speed % 1.0) - 0.5) * 2 * (hi - lo)) + lo
            cv2.rectangle(frame, (0, 0), (w - 1, h - 1), color, t)

        FN  = cv2.FONT_HERSHEY_SIMPLEX
        FND = cv2.FONT_HERSHEY_DUPLEX

        # Status HUD
        fill_alpha(0, h - 110, 290, h, (8, 8, 8), 0.6)
        st_c = {"IDLE": (80, 200, 80), "VALIDATING": (0, 220, 255), "WAITING_ON_HARDWARE": (0, 180, 100)}.get(self.current_state, (200,200,200))
        cv2.putText(frame, f"State:   {self.current_state}", (8, h - 88), FN, 0.5, st_c, 1)
        cv2.putText(frame, f"Dist:    {self.dist_val:.1f} cm",           (8, h - 66), FN, 0.5, (255, 220, 0), 1)
        cv2.putText(frame, f"Face:    {'Detected' if face_detected else 'None'}", (8, h - 44), FN, 0.5, (0, 200, 255) if face_detected else (60,60,60), 1)
        cv2.putText(frame, f"Gesture: {gesture}", (8, h - 22), FN, 0.5, (0, 255, 60) if gesture == "THUMB UP" else (60,60,60), 1)

        # ACCESS DENIED
        if self.current_state == "IDLE" and now < self._denied_flash_until:
            fill_alpha(0, 0, w, 65, (0, 0, 140), 0.75)
            cv2.putText(frame, "ACCESS DENIED", (w//2-140, 42), FND, 1.1, (255, 80, 80), 2)
            pulse_border((0, 0, 200), speed=3.5)

        # VALIDATING
        elif self.current_state == "VALIDATING":
            t_l = max(0.0, self.TIMEOUT_DURATION - (now - self.validation_start_time))
            fill_alpha(0, 0, w, 60, (0, 60, 100), 0.65)
            cv2.putText(frame, "SHOW CLOSED FIST TO UNLOCK", (w//2-180, 38), FN, 0.8, (0, 230, 255), 2)
            num = f"{t_l:.1f}s"
            (tw, th), _ = cv2.getTextSize(num, FND, 3.0, 4)
            cv2.putText(frame, num, (w//2-tw//2, h//2+th//2), FND, 3.0, (0, 220, 255) if t_l > 2 else (60, 60, 255), 4)
            if self.gesture_start_time:
                pct = min(1.0, (now - self.gesture_start_time) / 1.5)
                cv2.rectangle(frame, (0, h-12), (int(w*pct), h), (0, 255, 60), -1)
            pulse_border((0, 180, 255), speed=2.0)

        # WAITING_ON_HARDWARE
        elif self.current_state == "WAITING_ON_HARDWARE":
            el = now - self.hardware_start_time if self.hardware_start_time else 0
            t_l = max(0.0, self.DOOR_OPEN_DURATION - el)
            if t_l > 10.0:
                fill_alpha(0, 0, w, 62, (0, 80, 0), 0.65)
                cv2.putText(frame, f"ACCESS GRANTED | OPEN | {int(t_l)}s", (w//2-210, 38), FND, 0.85, (80, 255, 100), 2)
                pulse_border((0, 160, 0), speed=0.8)
            elif t_l > 5.0:
                flash = int(now * 2) % 2 == 0
                fill_alpha(0, 0, w, 62, (0, 85, 115) if flash else (0, 45, 65), 0.75)
                cv2.putText(frame, f"CLOSING IN {int(t_l)}s", (w//2-120, 38), FND, 1.0, (0, 220, 255), 2)
                pulse_border((0, 200, 255) if flash else (0, 70, 110), speed=2.2)
            else:
                flash = int(now * 3) % 2 == 0
                fill_alpha(0, 0, w, 62, (0, 0, 140) if flash else (0, 0, 55), 0.82)
                cv2.putText(frame, f"DOOR CLOSING! {int(t_l)+1}s", (w//2-160, 38), FND, 1.0, (255, 80, 80), 2)
                pulse_border((255, 80, 80), speed=4.5)

if __name__ == '__main__':
    AIBrain().run()
