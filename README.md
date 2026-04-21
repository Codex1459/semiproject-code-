#  AI Gesture Door Access System

A robust, distributed security system featuring **MediaPipe AI** for computer vision and a **Raspberry Pi** for precision hardware control.

---

## Key Features

-   **Distributed Architecture**: 
    -   **Laptop AI Brain**: Processes 720p video at 30fps using MediaPipe (Hand/Face/Distance).
    -   **Pi Hardware Server**: Manages PWM Servos, RGB LEDs, and Ultrasonic sensors over a local TCP socket.
-   **Conditional AI Cascade**: High-performance logic that only runs heavy AI models when a human is detected within 10-30cm range, saving up to 95% CPU idle load.
-   **Butter-Smooth Servo**: PWM-driven servo movement at 50Hz for continuous, non-jittery door operation.
-   **3-Phase Feedback**:
    -    **Phase 1**: Door fully open for 20s (Green LED).
    -   **Phase 2**: Warning transition — 5s yellow strobe with moderate beeps while door glides to 45°.
    -   **Phase 3**: Critical warning — 5s red flashing with urgent double-beeps while door glides to 90°.
-   **Rich HUD Display**: Professional camera overlay with pulsing borders, semi-transparent panels, and massive centered countdown digits.

---

##  Hardware Pinout (BCM)

| Component | Pin | Note |
| :--- | :--- | :--- |
| **Servo** | `GPIO 18` | Hardware PWM recommended |
| **Buzzer** | `GPIO 17` | Standard passive/active buzzer |
| **RGB LED (R)** | `GPIO 22` | Common Cathode |
| **RGB LED (G)** | `GPIO 27` | Common Cathode |
| **RGB LED (B)** | `GPIO 25` | Common Cathode |
| **Ultrasonic Trig** | `GPIO 23` | |
| **Ultrasonic Echo** | `GPIO 24` | Use voltage divider (3.3V) |

---

##  Installation

### 1. Raspberry Pi Setup
```bash
# Install pigpio for smooth PWM
sudo apt-get install pigpiod
sudo systemctl enable pigpiod
sudo systemctl start pigpiod

# Run the server
python3 server.py
```

### 2. Laptop (AI Brain) Setup
```bash
pip install opencv-python mediapipe
# Update SERVER_IP in main.py to your Pi's IP
python3 main.py
```

---

##  How to Use

1.  **Approach**: Move within **30cm** of the ultrasonic sensor.
2.  **Detection**: The system will detect your face and enter `VALIDATING` state (Cyan LED).
3.  **Unlock**: Show a **THUMB UP** gesture and hold it for **1.5 seconds**.
4.  **Access**: The door will open (Green LED). You have 20 seconds of free passage before the closing sequence begins.
5.  **Closing**: The system will warn you with Yellow/Red lights and beeps as the door glides shut.

---

*Designed for maximum reliability and premium user experience.*
