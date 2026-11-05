import logging
import os
import random

import cv2
import numpy as np

ASSET_DIR = os.path.join(os.path.dirname(__file__), "assets")


class MultiCrabTracker:
    def __init__(
        self, min_present=3, distance_thresh=150, missing_frames=25, recount_cooldown=60, detection_interval=3
    ):

        self.sift = cv2.SIFT_create(nfeatures=400)

        index_params = dict(algorithm=1, trees=2)
        search_params = dict(checks=16)
        self.matcher = cv2.FlannBasedMatcher(index_params, search_params)

        self.training_images = []
        self.tracked = []

        self.counter = 0
        self.next_id = 0

        self.min_present = min_present
        self.distance_thresh = distance_thresh
        self.max_missing = missing_frames
        self.recount_cooldown = recount_cooldown

        self.counting = True
        self.visible_count = 0

        self.detection_interval = detection_interval
        self.frame_index = 0

        self.scale = 0.75

    # ----------------------------
    # Load Training Image
    # ----------------------------
    def load_training_image(self, filepath):

        frame = cv2.imread(filepath)

        if frame is None:
            logging.debug("Failed to load training image")
            return False

        frame = cv2.resize(frame, (0, 0), fx=self.scale, fy=self.scale)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        kp, des = self.sift.detectAndCompute(gray, None)

        logging.debug("Keypoints: %s", len(kp))

        if des is None or len(kp) < 15:
            logging.debug("Not enough features")
            return False

        self.training_images.append(
            {
                "gray": gray,
                "kp": kp,
                "des": des,
                "color": (random.randint(80, 255), random.randint(80, 255), random.randint(80, 255)),
            }
        )

        logging.debug("Training image added")
        return True

    # ----------------------------
    # Detection
    # ----------------------------
    def detect(self, frame):

        self.frame_index += 1

        small = cv2.resize(frame, (0, 0), fx=self.scale, fy=self.scale)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

        detections = []

        if self.frame_index % self.detection_interval == 0:
            kp_frame, des_frame = self.sift.detectAndCompute(gray, None)

            if des_frame is not None and len(des_frame) >= 2:
                for train in self.training_images:
                    matches = self.matcher.knnMatch(train["des"], des_frame, k=2)

                    good = []
                    for m, n in matches:
                        if m.distance < 0.7 * n.distance:  # relaxed threshold
                            good.append(m)

                    if len(good) < 10:  # lowered requirement
                        continue

                    src = np.float32([train["kp"][m.queryIdx].pt for m in good]).reshape(-1, 1, 2)

                    dst = np.float32([kp_frame[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)

                    H, _ = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)

                    if H is None:
                        continue

                    h, w = train["gray"].shape

                    corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)

                    projected = cv2.perspectiveTransform(corners, H)
                    projected /= self.scale

                    cx = int(np.median(projected[:, 0, 0]))
                    cy = int(np.median(projected[:, 0, 1]))

                    detections.append({"centroid": (cx, cy), "bbox": projected, "color": train["color"]})

        self.update_tracked(detections)
        self.draw(frame)

        return frame

    # ----------------------------
    # Tracking
    # ----------------------------
    def update_tracked(self, detections):

        assigned = set()

        for crab in self.tracked:
            crab["missing"] += 1

            best_det = None
            best_dist = self.distance_thresh

            for i, det in enumerate(detections):
                if i in assigned:
                    continue

                dist = np.hypot(crab["centroid"][0] - det["centroid"][0], crab["centroid"][1] - det["centroid"][1])

                if dist < best_dist:
                    best_dist = dist
                    best_det = i

            if best_det is not None:
                det = detections[best_det]
                assigned.add(best_det)

                crab["centroid"] = det["centroid"]
                crab["bbox"] = det["bbox"]
                crab["color"] = det["color"]

                crab["frames"] += 1
                crab["missing"] = 0

        # New tracks
        for i, det in enumerate(detections):
            if i in assigned:
                continue

            self.tracked.append(
                {
                    "id": self.next_id,
                    "centroid": det["centroid"],
                    "bbox": det["bbox"],
                    "color": det["color"],
                    "frames": 1,
                    "missing": 0,
                    "counted": False,
                    "cooldown": 0,
                }
            )

            self.next_id += 1

        # Counting
        for crab in self.tracked:
            if crab["cooldown"] > 0:
                crab["cooldown"] -= 1

            if crab["frames"] >= self.min_present and not crab["counted"] and self.counting and crab["cooldown"] == 0:
                self.counter += 1
                crab["counted"] = True
                crab["cooldown"] = self.recount_cooldown

                logging.debug("Total crabs: %s", self.counter)

        # Remove lost
        self.tracked = [c for c in self.tracked if c["missing"] < self.max_missing]

    # ----------------------------
    # Drawing (WITH NEW UI)
    # ----------------------------
    def draw(self, frame):

        visible_crabs = 0

        for crab in self.tracked:
            if crab["frames"] < self.min_present:
                continue

            visible_crabs += 1

            box = crab["bbox"]

            x_min = int(np.min(box[:, 0, 0]))
            y_min = int(np.min(box[:, 0, 1]))
            x_max = int(np.max(box[:, 0, 0]))
            y_max = int(np.max(box[:, 0, 1]))

            cv2.rectangle(frame, (x_min, y_min), (x_max, y_max), crab["color"], 3)

            cv2.putText(
                frame, f"Crab {crab['id']}", (x_min, y_min - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, crab["color"], 2
            )

        # UI Panel
        cv2.rectangle(frame, (10, 10), (300, 100), (0, 0, 0), -1)

        cv2.putText(frame, f"Total: {self.counter}", (20, 45), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)

        cv2.putText(frame, f"In Frame: {visible_crabs}", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
        self.visible_count = visible_crabs

    def reset_counter(self):
        self.counter = 0
        self.next_id = 0
        self.tracked = []
        self.visible_count = 0


# ----------------------------
# MAIN LOOP
# ----------------------------
if __name__ == "__main__":
    tracker = MultiCrabTracker()

    tracker.load_training_image(os.path.join(ASSET_DIR, "monkeydo.png"))

    cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        logging.debug("Error: Could not open camera")
        exit()

    while True:
        ret, frame = cap.read()
        if not ret:
            continue

        frame = tracker.detect(frame)

        cv2.imshow("Crab Tracker", frame)

        key = cv2.waitKey(10) & 0xFF

        if key == ord("q"):
            break

        if key == 32:
            tracker.counting = not tracker.counting

    cap.release()
    cv2.destroyAllWindows()
