import cv2
import mediapipe as mp
import socket
import time

SERVER_IP = '192.168.1.3' # Update with actual Pi IP
SERVER_PORT = 5005

class AIBrain:
    def __init__(self):
        self.mp_hands = mp.solutions.hands
        self.mp_face = mp.solutions.face_detection
        self.mp_drawing = mp.solutions.drawing_utils
        
        # Initialize AI Models
        self.hands = self.mp_hands.Hands(
            min_detection_confidence=0.7,
            min_tracking_confidence=0.5,
            max_num_hands=1
        )
        self.face_detection = self.mp_face.FaceDetection(
            min_detection_confidence=0.5
        )
        
        self.sock = None
        self.current_state = "IDLE"
        self.validation_start_time = 0
        self.gesture_start_time = None
        self.hardware_start_time = None
        self.TIMEOUT_DURATION = 5.0
        self.DOOR_OPEN_DURATION = 30.0
        self.DOOR_WARNING_SECS = 10.0
        self.last_ping = 0
        self.dist_val = -1.0

    def connect_to_server(self):
        print(f"Connecting to {SERVER_IP}:{SERVER_PORT}...")
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect((SERVER_IP, SERVER_PORT))
            self.sock = s
            print("Connected to Pi Hardware Server!")
            return True
        except Exception as e:
            print(f"Connection failed: {e}")
            self.sock = None
            return False

    def send_command(self, cmd):
        if not self.sock:
            return None
        try:
            self.sock.sendall((cmd + '\n').encode('utf-8'))
            return self.sock.recv(1024).decode('utf-8').strip()
        except Exception as e:
            print(f"Socket error: {e}")
            self.sock = None # Disconnect to force reconnect
            return None

    def is_thumb_up(self, hand_landmarks):
        """ Evaluates if the hand gesture is a Thumb Up """
        lm = hand_landmarks.landmark
        thumb_extended = lm[4].y < lm[2].y
        index_curled = lm[8].y > lm[5].y
        middle_curled = lm[12].y > lm[9].y
        ring_curled = lm[16].y > lm[13].y
        pinky_curled = lm[20].y > lm[17].y
        return thumb_extended and index_curled and middle_curled and ring_curled and pinky_curled

    def run(self):
        while not self.connect_to_server():
            print("Waiting for server to come online... Update SERVER_IP if necessary.")
            time.sleep(2)
            
        cap = cv2.VideoCapture(0)
        print("Starting MIDNIGHT TECHIE AI Brain...")

        while True:
            # Robust reconnect logic
            if not self.sock:
                print("Lost connection. Reconnecting...")
                if not self.connect_to_server():
                    time.sleep(1)
                    continue

            ret, frame = cap.read()
            if not ret: break
            
            frame = cv2.flip(frame, 1)
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            
            # --- CONDITIONAL PROCESSING CASCADE ---
            
            # 1. Distance Check (Low CPU)
            if time.time() - self.last_ping > 0.2:
                resp = self.send_command("GET_DIST")
                self.last_ping = time.time()
                if resp:
                    if resp != "BUSY":
                        try:
                            self.dist_val = float(resp)
                        except ValueError:
                            pass

            face_detected = False
            gesture = "None"

            # 2. Face Detection (Medium CPU) - ONLY run if someone is near
            if 10.0 <= self.dist_val <= 30.0:
                face_results = self.face_detection.process(rgb_frame)
                if face_results.detections:
                    face_detected = True
                    for detection in face_results.detections:
                        self.mp_drawing.draw_detection(frame, detection)
                
                if self.current_state == "IDLE" and face_detected:
                    self.current_state = "VALIDATING"
                    self.validation_start_time = time.time()
                    self.send_command("STATE:VALIDATING")
                    print("HUMAN IN RANGE & FACE DETECTED: Validating gesture...")

                # 3. Hand Tracking (High CPU) - ONLY run if we are validating a face
                elif self.current_state == "VALIDATING":
                    hand_results = self.hands.process(rgb_frame)
                    if hand_results.multi_hand_landmarks:
                        for hand_landmarks in hand_results.multi_hand_landmarks:
                            self.mp_drawing.draw_landmarks(frame, hand_landmarks, self.mp_hands.HAND_CONNECTIONS)
                            if self.is_thumb_up(hand_landmarks):
                                gesture = "THUMB UP"
                            else:
                                gesture = "UNKNOWN HAND"

                    # Gesture Holding Logic
                    if gesture == "THUMB UP":
                        if self.gesture_start_time is None:
                            self.gesture_start_time = time.time()
                        elif time.time() - self.gesture_start_time >= 1.5:
                            self.send_command("ACTION:OPEN")
                            self.current_state = "WAITING_ON_HARDWARE"
                            self.hardware_start_time = time.time()  # Record door-open time
                            self.gesture_start_time = None
                            print("ACCESS GRANTED.")
                    else:
                        self.gesture_start_time = None
                        
                        # On timeout: go straight back to IDLE (no lockout)
                        if time.time() - self.validation_start_time > self.TIMEOUT_DURATION:
                            self.send_command("ACTION:DENIED")
                            self.current_state = "IDLE"
                            print("ACCESS DENIED: Returning to IDLE.")
            else:
                # User stepped out of range
                if self.current_state == "VALIDATING":
                    self.current_state = "IDLE"
                    self.gesture_start_time = None
                    self.send_command("STATE:IDLE")
                    print("HUMAN LOST: Returning to IDLE")
                elif self.current_state == "WAITING_ON_HARDWARE":
                    if resp != "BUSY":
                        self.current_state = "IDLE"
                        self.hardware_start_time = None

            # ============================================================
            # DISPLAY OVERLAY — Rich visual feedback
            # ============================================================
            h, w = frame.shape[:2]
            now = time.time()

            # Helper: semi-transparent filled rectangle
            def draw_rect_alpha(x1, y1, x2, y2, color, alpha=0.6):
                sub = frame[y1:y2, x1:x2]
                colored = sub.copy()
                colored[:] = color
                cv2.addWeighted(colored, alpha, sub, 1 - alpha, 0, sub)
                frame[y1:y2, x1:x2] = sub

            # Helper: pulsing animated border
            def draw_pulse_border(color, speed=1.0, min_t=3, max_t=10):
                t = int(abs((now * speed % 1.0) - 0.5) * 2 * (max_t - min_t)) + min_t
                cv2.rectangle(frame, (0, 0), (w - 1, h - 1), color, t)

            # ---- STATUS HUD — always visible, bottom-left corner ----
            draw_rect_alpha(0, h - 108, 290, h, (10, 10, 10), 0.55)
            cv2.putText(frame, f"State:   {self.current_state}", (8, h - 86), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 255, 120), 1)
            cv2.putText(frame, f"Dist:    {self.dist_val:.1f} cm",  (8, h - 64), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 220, 0), 1)
            cv2.putText(frame, f"Face:    {'Detected' if face_detected else 'None'}", (8, h - 42), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 200, 255) if face_detected else (80, 80, 80), 1)
            cv2.putText(frame, f"Gesture: {gesture}", (8, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 255, 60) if gesture == "THUMB UP" else (80, 80, 80), 1)

            # ============================================================
            # STATE: ACCESS DENIED — brief red flash
            # ============================================================
            if self.current_state == "IDLE" and hasattr(self, '_denied_flash_until') and now < self._denied_flash_until:
                draw_rect_alpha(0, 0, w, 65, (0, 0, 140), 0.75)
                cv2.putText(frame, "ACCESS DENIED", (w // 2 - 160, 40), cv2.FONT_HERSHEY_DUPLEX, 1.2, (255, 80, 80), 2)
                cv2.putText(frame, "Try again — show THUMB UP", (w // 2 - 148, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 180, 180), 1)
                draw_pulse_border((0, 0, 200), speed=3, min_t=4, max_t=12)

            # ============================================================
            # STATE: VALIDATING — gesture timer + hold arc
            # ============================================================
            elif self.current_state == "VALIDATING":
                time_left = max(0.0, self.TIMEOUT_DURATION - (now - self.validation_start_time))

                # Top banner
                draw_rect_alpha(0, 0, w, 60, (0, 60, 100), 0.65)
                cv2.putText(frame, "SHOW  THUMB UP  TO UNLOCK", (w // 2 - 185, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 230, 255), 2)

                # Large countdown digit in center
                big_num = f"{time_left:.1f}s"
                (tw, th), _ = cv2.getTextSize(big_num, cv2.FONT_HERSHEY_DUPLEX, 3.0, 4)
                draw_rect_alpha(w // 2 - tw // 2 - 18, h // 2 - th - 15, w // 2 + tw // 2 + 18, h // 2 + 22, (0, 30, 50), 0.5)
                digit_color = (0, 220, 255) if time_left > 2 else (0, 80, 255)
                cv2.putText(frame, big_num, (w // 2 - tw // 2, h // 2 + th // 2 - 5), cv2.FONT_HERSHEY_DUPLEX, 3.0, digit_color, 4)

                # Timeout bar (bottom strip)
                bar_w = int(w * (time_left / self.TIMEOUT_DURATION))
                cv2.rectangle(frame, (0, h - 14), (w, h), (20, 20, 20), -1)
                cv2.rectangle(frame, (0, h - 14), (bar_w, h), (0, 200, 255) if time_left > 2 else (0, 60, 255), -1)

                # Hold-progress bar (second strip, visible when gesture detected)
                if self.gesture_start_time is not None:
                    hold_pct = min(1.0, (now - self.gesture_start_time) / 1.5)
                    hold_w = int(w * hold_pct)
                    cv2.rectangle(frame, (0, h - 32), (w, h - 16), (20, 40, 20), -1)
                    cv2.rectangle(frame, (0, h - 32), (hold_w, h - 16), (0, 255, 60), -1)
                    cv2.putText(frame, f"HOLD {int(hold_pct * 100)}%", (w // 2 - 40, h - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0, 255, 80), 1)

                draw_pulse_border((0, 180, 255), speed=1.5, min_t=3, max_t=8)

            # ============================================================
            # STATE: WAITING_ON_HARDWARE — 3-phase door countdown
            # Phase 1: >10s  → Green
            # Phase 2: 5-10s → Yellow (door 0°→45°)
            # Phase 3: 0-5s  → Red flash (door 45°→90°)
            # ============================================================
            elif self.current_state == "WAITING_ON_HARDWARE":
                elapsed = now - self.hardware_start_time if self.hardware_start_time else 0
                time_left = max(0.0, self.DOOR_OPEN_DURATION - elapsed)

                if time_left > 10.0:
                    # PHASE 1: Green — door fully open
                    draw_rect_alpha(0, 0, w, 62, (0, 80, 0), 0.65)
                    cv2.putText(frame, "ACCESS GRANTED  DOOR OPEN", (w // 2 - 185, 36), cv2.FONT_HERSHEY_DUPLEX, 0.95, (80, 255, 100), 2)
                    cv2.putText(frame, f"Closing in {int(time_left)}s", (w // 2 - 65, 56), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 255, 180), 1)
                    cv2.rectangle(frame, (0, h - 12), (w, h), (10, 40, 10), -1)
                    cv2.rectangle(frame, (0, h - 12), (int(w * time_left / self.DOOR_OPEN_DURATION), h), (0, 210, 60), -1)
                    draw_pulse_border((0, 180, 0), speed=0.8, min_t=3, max_t=7)

                elif time_left > 5.0:
                    # PHASE 2: Yellow — door slowly closing
                    flash = int(now * 2) % 2 == 0
                    draw_rect_alpha(0, 0, w, 62, (0, 90, 120) if flash else (0, 50, 70), 0.75)
                    cv2.putText(frame, f"CLOSING IN  {int(time_left)}s", (w // 2 - 148, 38), cv2.FONT_HERSHEY_DUPLEX, 1.05, (0, 220, 255), 2)
                    cv2.putText(frame, "Door is closing — GET INSIDE!", (w // 2 - 162, 57), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 240, 255), 1)

                    big = f"{int(time_left)}"
                    (tw, th), _ = cv2.getTextSize(big, cv2.FONT_HERSHEY_DUPLEX, 5.0, 7)
                    draw_rect_alpha(w // 2 - tw // 2 - 20, h // 2 - th - 18, w // 2 + tw // 2 + 20, h // 2 + 26, (0, 40, 60), 0.5)
                    cv2.putText(frame, big, (w // 2 - tw // 2, h // 2 + th // 2 - 10), cv2.FONT_HERSHEY_DUPLEX, 5.0, (0, 220, 255), 7)

                    cv2.rectangle(frame, (0, h - 12), (w, h), (10, 30, 40), -1)
                    cv2.rectangle(frame, (0, h - 12), (int(w * time_left / self.DOOR_OPEN_DURATION), h), (0, 180, 220), -1)
                    draw_pulse_border((0, 200, 255) if flash else (0, 80, 120), speed=2, min_t=4, max_t=10)

                else:
                    # PHASE 3: Red — door rapidly closing, FINAL WARNING
                    flash = int(now * 3) % 2 == 0
                    draw_rect_alpha(0, 0, w, 62, (0, 0, 150) if flash else (0, 0, 60), 0.82)
                    warn_color = (100, 100, 255) if flash else (255, 100, 100)
                    cv2.putText(frame, f"DOOR CLOSING! {int(time_left)+1}s", (w // 2 - 188, 38), cv2.FONT_HERSHEY_DUPLEX, 1.05, warn_color, 2)
                    cv2.putText(frame, "FINAL WARNING — GET INSIDE NOW!", (w // 2 - 192, 57), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (220, 200, 255), 1)

                    big = f"{int(time_left)+1}"
                    (tw, th), _ = cv2.getTextSize(big, cv2.FONT_HERSHEY_DUPLEX, 7.0, 10)
                    draw_rect_alpha(w // 2 - tw // 2 - 22, h // 2 - th - 20, w // 2 + tw // 2 + 22, h // 2 + 32, (40, 0, 0), 0.55)
                    cv2.putText(frame, big, (w // 2 - tw // 2, h // 2 + th // 2 - 15), cv2.FONT_HERSHEY_DUPLEX, 7.0, warn_color, 10)

                    cv2.rectangle(frame, (0, h - 12), (w, h), (30, 10, 10), -1)
                    bar_color = (80, 60, 255) if flash else (200, 40, 40)
                    cv2.rectangle(frame, (0, h - 12), (int(w * time_left / self.DOOR_OPEN_DURATION), h), bar_color, -1)
                    draw_pulse_border(warn_color, speed=4, min_t=5, max_t=14)

            cv2.imshow("MIDNIGHT TECHIE - AI Brain", frame)

            
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
                
        cap.release()
        cv2.destroyAllWindows()
        if self.sock:
            self.sock.close()

if __name__ == '__main__':
    brain = AIBrain()
    brain.run()
