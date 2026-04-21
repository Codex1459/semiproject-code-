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
        self.hardware_start_time = None  # Tracks when door opened
        self.TIMEOUT_DURATION = 5.0
        self.LOCKOUT_DURATION = 60.0
        self.DOOR_OPEN_DURATION = 30.0  # Total door open time (must match server.py)
        self.DOOR_WARNING_SECS = 10.0   # Last N seconds show closing warning
        self.lockout_end_time = 0
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
                    if resp == "LOCKED":
                        if self.current_state != "LOCKED":
                            # First time we hear LOCKED — record when lockout expires
                            self.lockout_end_time = time.time() + self.LOCKOUT_DURATION
                        self.current_state = "LOCKED"
                    elif resp != "BUSY":
                        try:
                            self.dist_val = float(resp)
                        except ValueError:
                            pass

            face_detected = False
            gesture = "None"

            if self.current_state == "LOCKED":
                if resp and resp != "LOCKED":
                    self.current_state = "IDLE"
            
            # 2. Face Detection (Medium CPU) - ONLY run if someone is near
            elif 10.0 <= self.dist_val <= 30.0:
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
                        self.gesture_start_time = None # Reset holding timer
                        
                        # Handle Timeout
                        if time.time() - self.validation_start_time > self.TIMEOUT_DURATION:
                            self.send_command("ACTION:DENIED")
                            self.current_state = "LOCKED"
                            print("ACCESS DENIED: Gesture validity timeout.")
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

            # ---- DISPLAY OVERLAY ----
            h, w = frame.shape[:2]

            # Static status lines
            cv2.putText(frame, f"State: {self.current_state}",    (10, 30),  cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            cv2.putText(frame, f"Dist:  {self.dist_val:.1f} cm", (10, 60),  cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
            cv2.putText(frame, f"Face:  {'Detected' if face_detected else 'None'}", (10, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 140, 0) if face_detected else (0, 0, 255), 2)
            cv2.putText(frame, f"Gesture: {gesture}", (10, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0) if gesture == "THUMB UP" else (0, 0, 255), 2)

            now = time.time()

            # --- LOCKED: Countdown until they can retry ---
            if self.current_state == "LOCKED":
                remaining = max(0.0, self.lockout_end_time - now)
                countdown_text = f"LOCKED  Retry in {int(remaining)+1}s"
                # Red banner across the top
                cv2.rectangle(frame, (0, 0), (w, 50), (0, 0, 180), -1)
                cv2.putText(frame, countdown_text, (10, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
                # Countdown progress bar (drains left-to-right)
                bar_w = int(w * (remaining / self.LOCKOUT_DURATION))
                cv2.rectangle(frame, (0, 50), (w, 58), (40, 40, 40), -1)
                cv2.rectangle(frame, (0, 50), (bar_w, 58), (0, 0, 255), -1)

            # --- VALIDATING: Show time left to gesture + hold-progress bar ---
            elif self.current_state == "VALIDATING":
                time_left = max(0.0, self.TIMEOUT_DURATION - (now - self.validation_start_time))
                timeout_text = f"Show THUMB UP!  {time_left:.1f}s left"
                cv2.putText(frame, timeout_text, (10, h - 60), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 220, 255), 2)

                # Timeout bar (drains right-to-left across the bottom)
                bar_w = int(w * (time_left / self.TIMEOUT_DURATION))
                cv2.rectangle(frame, (0, h - 20), (w, h), (40, 40, 40), -1)
                cv2.rectangle(frame, (0, h - 20), (bar_w, h), (0, 200, 255), -1)

                # Gesture hold-progress bar (fills left-to-right when thumb is up)
                if self.gesture_start_time is not None:
                    hold_pct = min(1.0, (now - self.gesture_start_time) / 1.5)
                    hold_w = int(w * hold_pct)
                    cv2.rectangle(frame, (0, h - 38), (w, h - 22), (40, 40, 40), -1)
                    cv2.rectangle(frame, (0, h - 38), (hold_w, h - 22), (0, 255, 0), -1)
                    cv2.putText(frame, "Hold...", (10, h - 42), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 1)

            # --- WAITING_ON_HARDWARE: Door is open, show countdown and closing warning ---
            elif self.current_state == "WAITING_ON_HARDWARE":
                elapsed = now - self.hardware_start_time if self.hardware_start_time else 0
                time_left = max(0.0, self.DOOR_OPEN_DURATION - elapsed)
                warning_phase = time_left <= self.DOOR_WARNING_SECS

                # Flash the banner yellow/dark in the last 10 seconds
                if warning_phase:
                    flash = int(now * 2) % 2 == 0  # Toggle every 0.5s
                    banner_color = (0, 165, 255) if flash else (0, 80, 120)
                    msg = f"WARNING! Door closing in {int(time_left)+1}s  GET INSIDE!"
                    cv2.rectangle(frame, (0, 0), (w, 55), banner_color, -1)
                    cv2.putText(frame, msg, (10, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
                else:
                    cv2.rectangle(frame, (0, 0), (w, 55), (0, 140, 0), -1)
                    cv2.putText(frame, f"ACCESS GRANTED  Door open for {int(time_left)}s more", (10, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2)

                # Door-open time remaining bar at the bottom
                bar_w = int(w * (time_left / self.DOOR_OPEN_DURATION))
                cv2.rectangle(frame, (0, h - 12), (w, h), (40, 40, 40), -1)
                bar_color = (0, 60, 255) if warning_phase else (0, 200, 0)
                cv2.rectangle(frame, (0, h - 12), (bar_w, h), bar_color, -1)
            
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
