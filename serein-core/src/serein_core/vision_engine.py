"""
Vision Engine - Computer vision capabilities for SereinOS.
Screen capture, OCR, object detection, camera input.
"""
from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any, Tuple
import os
import time
import threading
from dataclasses import dataclass
from pathlib import Path
import numpy as np


@dataclass
class VisionConfig:
    """Vision module configuration."""
    enabled: bool = False
    camera_index: int = 0
    ocr_backend: str = "tesseract"  # tesseract, easyocr, paddleocr
    ocr_languages: List[str] = None
    detection_backend: str = "opencv"  # opencv, ultralytics, torchvision
    screen_capture_backend: str = "mss"  # mss, pyautogui, pil
    capture_interval: float = 1.0  # seconds between captures
    
    def __post_init__(self):
        if self.ocr_languages is None:
            self.ocr_languages = ["en"]


class VisionBackend(ABC):
    """Abstract base class for vision backends."""
    
    @abstractmethod
    def is_available(self) -> bool:
        pass
    
    @property
    @abstractmethod
    def name(self) -> str:
        pass


class ScreenCapture:
    """Screen capture functionality."""
    
    def __init__(self, backend: str = "mss"):
        self.backend = backend
        self._capturer = None
        self._init_capturer()
    
    def _init_capturer(self):
        if self.backend == "mss":
            try:
                import mss
                self._capturer = mss.mss()
            except ImportError:
                pass
        elif self.backend == "pyautogui":
            try:
                import pyautogui
                self._capturer = pyautogui
            except ImportError:
                pass
        elif self.backend == "pil":
            try:
                from PIL import ImageGrab
                self._capturer = ImageGrab
            except ImportError:
                pass
    
    def is_available(self) -> bool:
        return self._capturer is not None
    
    def capture(self, region: Tuple[int, int, int, int] = None) -> Optional[np.ndarray]:
        """Capture screen region (left, top, width, height) or full screen."""
        if not self._capturer:
            return None
        
        try:
            if self.backend == "mss":
                if region:
                    monitor = {"left": region[0], "top": region[1], 
                              "width": region[2], "height": region[3]}
                else:
                    monitor = self._capturer.monitors[1]  # Primary monitor
                img = np.array(self._capturer.grab(monitor))
                return img[:, :, :3]  # Drop alpha channel
            
            elif self.backend == "pyautogui":
                import pyautogui
                if region:
                    img = pyautogui.screenshot(region=region)
                else:
                    img = pyautogui.screenshot()
                return np.array(img)
            
            elif self.backend == "pil":
                from PIL import ImageGrab
                if region:
                    bbox = (region[0], region[1], region[0]+region[2], region[1]+region[3])
                    img = ImageGrab.grab(bbox=bbox)
                else:
                    img = ImageGrab.grab()
                return np.array(img)
        except Exception as e:
            print(f"[ScreenCapture] Error: {e}")
        
        return None
    
    def capture_to_file(self, filepath: str, region: Tuple = None) -> bool:
        """Capture screen and save to file."""
        img = self.capture(region)
        if img is not None:
            try:
                from PIL import Image
                Image.fromarray(img).save(filepath)
                return True
            except Exception as e:
                print(f"[ScreenCapture] Save error: {e}")
        return False


class OCRBackend(VisionBackend):
    """OCR backends."""
    
    def __init__(self, backend: str = "tesseract", languages: List[str] = None):
        self.backend_type = backend
        self.languages = languages or ["en"]
        self._ocr = None
        self._init_ocr()
    
    def _init_ocr(self):
        if self.backend_type == "tesseract":
            try:
                import pytesseract
                # Test if tesseract is available
                pytesseract.get_tesseract_version()
                self._ocr = pytesseract
            except Exception:
                pass
        elif self.backend_type == "easyocr":
            try:
                import easyocr
                self._ocr = easyocr.Reader(self.languages)
            except ImportError:
                pass
        elif self.backend_type == "paddleocr":
            try:
                from paddleocr import PaddleOCR
                self._ocr = PaddleOCR(use_angle_cls=True, lang=self.languages[0])
            except ImportError:
                pass
    
    def is_available(self) -> bool:
        return self._ocr is not None
    
    @property
    def name(self) -> str:
        return f"ocr-{self.backend_type}"
    
    def extract_text(self, image: np.ndarray) -> str:
        """Extract text from image array."""
        if not self._ocr:
            return ""
        
        try:
            if self.backend_type == "tesseract":
                lang = "+".join(self.languages)
                return self._ocr.image_to_string(image, lang=lang).strip()
            
            elif self.backend_type == "easyocr":
                results = self._ocr.readtext(image)
                return " ".join([r[1] for r in results]).strip()
            
            elif self.backend_type == "paddleocr":
                results = self._ocr.ocr(image, cls=True)
                if results and results[0]:
                    return " ".join([line[1][0] for line in results[0]]).strip()
        except Exception as e:
            print(f"[OCR] Error: {e}")
        
        return ""


class CameraInput:
    """Camera input handling."""
    
    def __init__(self, camera_index: int = 0):
        self.camera_index = camera_index
        self._cap = None
        self._init_camera()
    
    def _init_camera(self):
        try:
            import cv2
            self._cap = cv2.VideoCapture(self.camera_index)
            if not self._cap.isOpened():
                self._cap = None
        except ImportError:
            pass
        except Exception as e:
            print(f"[CameraInput] Error: {e}")
    
    def is_available(self) -> bool:
        return self._cap is not None and self._cap.isOpened()
    
    def read_frame(self) -> Optional[np.ndarray]:
        """Read a single frame."""
        if not self.is_available():
            return None
        
        import cv2
        ret, frame = self._cap.read()
        if ret:
            return frame
        return None
    
    def capture_photo(self, filepath: str) -> bool:
        """Capture and save photo."""
        frame = self.read_frame()
        if frame is not None:
            try:
                import cv2
                cv2.imwrite(filepath, frame)
                return True
            except Exception as e:
                print(f"[CameraInput] Save error: {e}")
        return False
    
    def release(self):
        if self._cap:
            self._cap.release()
            self._cap = None


class ObjectDetector(VisionBackend):
    """Object detection backend."""
    
    def __init__(self, backend: str = "opencv"):
        self.backend_type = backend
        self._detector = None
        self._class_names = []
        self._init_detector()
    
    def _init_detector(self):
        if self.backend_type == "opencv":
            try:
                import cv2
                # Use DNN module with pre-trained models
                self._detector = cv2.dnn.readNetFromTensorflow(
                    "models/opencv/frozen_inference_graph.pb",
                    "models/opencv/ssd_mobilenet_v2_coco_2018_03_29.pbtxt"
                )
                self._class_names = [
                    "background", "person", "bicycle", "car", "motorcycle", "airplane",
                    "bus", "train", "truck", "boat", "traffic light", "fire hydrant"
                    # ... more COCO classes
                ]
            except Exception:
                pass
        elif self.backend_type == "ultralytics":
            try:
                from ultralytics import YOLO
                self._detector = YOLO("yolov8n.pt")
            except ImportError:
                pass
    
    def is_available(self) -> bool:
        return self._detector is not None
    
    @property
    def name(self) -> str:
        return f"detector-{self.backend_type}"
    
    def detect(self, image: np.ndarray, confidence_threshold: float = 0.5) -> List[Dict]:
        """Detect objects in image."""
        if not self._detector:
            return []
        
        detections = []
        
        try:
            if self.backend_type == "opencv":
                import cv2
                h, w = image.shape[:2]
                blob = cv2.dnn.blobFromImage(image, size=(300, 300), swapRB=True)
                self._detector.setInput(blob)
                detections_raw = self._detector.forward()
                
                for i in range(detections_raw.shape[2]):
                    confidence = detections_raw[0, 0, i, 2]
                    if confidence > confidence_threshold:
                        class_id = int(detections_raw[0, 0, i, 1])
                        box = detections_raw[0, 0, i, 3:7] * np.array([w, h, w, h])
                        x1, y1, x2, y2 = box.astype(int)
                        detections.append({
                            "class": self._class_names[class_id] if class_id < len(self._class_names) else str(class_id),
                            "confidence": float(confidence),
                            "bbox": [int(x1), int(y1), int(x2), int(y2)]
                        })
            
            elif self.backend_type == "ultralytics":
                results = self._detector(image)[0]
                for box in results.boxes:
                    if box.conf[0] > confidence_threshold:
                        detections.append({
                            "class": results.names[int(box.cls[0])],
                            "confidence": float(box.conf[0]),
                            "bbox": box.xyxy[0].tolist()
                        })
        except Exception as e:
            print(f"[ObjectDetector] Error: {e}")
        
        return detections


class VisionEngine:
    """Main vision engine coordinating all vision capabilities."""
    
    def __init__(self, config: VisionConfig = None):
        self.config = config or VisionConfig()
        self.screen_capture = ScreenCapture(self.config.screen_capture_backend)
        self.ocr = OCRBackend(self.config.ocr_backend, self.config.ocr_languages)
        self.camera = CameraInput(self.config.camera_index)
        self.detector = ObjectDetector(self.config.detection_backend)
        self._monitoring = False
        self._monitor_thread = None
        self._callbacks: Dict[str, List] = {
            "on_screen_change": [],
            "on_text_detected": [],
            "on_object_detected": []
        }
    
    def is_available(self) -> bool:
        return any([
            self.screen_capture.is_available(),
            self.ocr.is_available(),
            self.camera.is_available(),
            self.detector.is_available()
        ])
    
    def on(self, event: str, callback):
        if event in self._callbacks:
            self._callbacks[event].append(callback)
    
    def _emit(self, event: str, *args):
        for cb in self._callbacks.get(event, []):
            try:
                cb(*args)
            except Exception as e:
                print(f"[VisionEngine] Callback error: {e}")
    
    def capture_screen(self, region: Tuple = None) -> Optional[np.ndarray]:
        """Capture current screen."""
        return self.screen_capture.capture(region)
    
    def extract_text_from_screen(self, region: Tuple = None) -> str:
        """Capture screen and extract text."""
        img = self.capture_screen(region)
        if img is not None:
            text = self.ocr.extract_text(img)
            if text:
                self._emit("on_text_detected", text, region)
            return text
        return ""
    
    def detect_objects_on_screen(self, region: Tuple = None) -> List[Dict]:
        """Detect objects on screen."""
        img = self.capture_screen(region)
        if img is not None:
            detections = self.detector.detect(img)
            if detections:
                self._emit("on_object_detected", detections, region)
            return detections
        return []
    
    def read_camera(self) -> Optional[np.ndarray]:
        """Read frame from camera."""
        return self.camera.read_frame()
    
    def extract_text_from_camera(self) -> str:
        """Extract text from camera frame."""
        frame = self.read_camera()
        if frame is not None:
            return self.ocr.extract_text(frame)
        return ""
    
    def detect_objects_camera(self) -> List[Dict]:
        """Detect objects from camera."""
        frame = self.read_camera()
        if frame is not None:
            return self.detector.detect(frame)
        return []
    
    def start_monitoring(self, interval: float = None):
        """Start continuous screen monitoring."""
        if self._monitoring:
            return
        
        self._monitoring = True
        interval = interval or self.config.capture_interval
        self._last_screen_text = ""
        
        def monitor_loop():
            while self._monitoring:
                try:
                    text = self.extract_text_from_screen()
                    if text and text != self._last_screen_text:
                        self._last_screen_text = text
                        self._emit("on_screen_change", text)
                except Exception as e:
                    print(f"[VisionEngine] Monitor error: {e}")
                time.sleep(interval)
        
        self._monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        self._monitor_thread.start()
    
    def stop_monitoring(self):
        """Stop screen monitoring."""
        self._monitoring = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=2)
    
    def get_status(self) -> Dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "screen_capture": self.screen_capture.is_available(),
            "ocr": self.ocr.is_available(),
            "ocr_backend": self.ocr.name,
            "camera": self.camera.is_available(),
            "detector": self.detector.is_available(),
            "detector_backend": self.detector.name,
            "monitoring": self._monitoring
        }