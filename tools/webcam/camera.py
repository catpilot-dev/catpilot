import cv2 as cv
import numpy as np

class Camera:
  def __init__(self, cam_type_state, stream_type, camera_id):
    try:
      camera_id = int(camera_id)
    except ValueError: # allow strings, ex: /dev/video0
      pass
    self.cam_type_state = cam_type_state
    self.stream_type = stream_type
    self.cur_frame_id = 0

    print(f"Opening {cam_type_state} at {camera_id}")

    self.cap = cv.VideoCapture(camera_id, cv.CAP_V4L2)

    # Use MJPG format — YUYV 720p is limited to 10fps on most USB cameras
    self.cap.set(cv.CAP_PROP_FOURCC, cv.VideoWriter_fourcc(*'MJPG'))
    self.cap.set(cv.CAP_PROP_FRAME_WIDTH, 1280.0)
    self.cap.set(cv.CAP_PROP_FRAME_HEIGHT, 720.0)
    self.cap.set(cv.CAP_PROP_FPS, 30.0)

    self.W = int(self.cap.get(cv.CAP_PROP_FRAME_WIDTH))
    self.H = int(self.cap.get(cv.CAP_PROP_FRAME_HEIGHT))
    actual_fourcc = int(self.cap.get(cv.CAP_PROP_FOURCC))
    print(f"  resolution: {self.W}x{self.H}, fourcc: {actual_fourcc:#010x}")

  @staticmethod
  def bgr2nv12(bgr):
    # BGR → YUV I420 via OpenCV, then rearrange to NV12
    i420 = cv.cvtColor(bgr, cv.COLOR_BGR2YUV_I420)
    h, w = bgr.shape[:2]
    # I420 layout: Y (w*h), U (w/2 * h/2), V (w/2 * h/2)
    # NV12 layout: Y (w*h), UV interleaved (w * h/2)
    y = i420[:h, :]
    uv_start = h
    u = i420[uv_start:uv_start + h // 4, :].reshape(h // 2, w // 2)
    v = i420[uv_start + h // 4:, :].reshape(h // 2, w // 2)
    uv = np.empty((h // 2, w), dtype=np.uint8)
    uv[:, 0::2] = u
    uv[:, 1::2] = v
    return np.vstack([y, uv])

  def read_frames(self):
    while True:
      ret, frame = self.cap.read()
      if not ret:
        break
      # Rotate the frame 180 degrees (flip both axes)
      frame = cv.flip(frame, -1)
      yuv = Camera.bgr2nv12(frame)
      yield yuv.data.tobytes()
    self.cap.release()
