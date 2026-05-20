#!/usr/bin/env python3
"""
macOS 内置摄像头拍照工具
用 AVFoundation 通过 PyObjC 拍照
"""
import os, sys, time
import AVFoundation
from AppKit import NSApp, NSApplication, NSAppKitVersionNumber, NSAppKitVersionNumber10_15
import objc
from PyObjCTools import AppHelper
import threading

class PhotoCaptureDelegate(NSObject):
    """拍照回调代理"""
    def init(self):
        self = objc.super(PhotoCaptureDelegate, self).init()
        if self:
            self.image_data = None
            self.captured = threading.Event()
        return self
    
    def captureOutput_didFinishProcessingPhoto_error_(self, output, photo, error):
        if error:
            print(f"❌ Capture error: {error}")
            self.captured.set()
            return
        self.image_data = photo.fileDataRepresentation()
        self.captured.set()

def capture_photo(output_path):
    """拍照保存到指定路径"""
    session = AVFoundation.AVCaptureSession.alloc().init()
    session.setSessionPreset_(AVFoundation.AVCaptureSessionPreset1280x720)
    
    # 找摄像头
    discovery = AVFoundation.AVCaptureDevice.DiscoverySession.alloc().initWithDeviceTypes_mediaType_position_(
        [AVFoundation.AVCaptureDeviceTypeBuiltInWideAngleCamera, 
         AVFoundation.AVCaptureDeviceTypeExternal],
        AVFoundation.AVMediaTypeVideo,
        AVFoundation.AVCaptureDevicePositionUnspecified
    )
    devices = discovery.devices()
    if not devices:
        print("❌ No camera found")
        return False
    
    camera = devices[0]
    print(f"📷 Camera: {camera.localizedName()}")
    
    # 输入
    input_obj, error = AVFoundation.AVCaptureDeviceInput.deviceInputWithDevice_error_(camera, None)
    if error:
        print(f"❌ Input error: {error}")
        return False
    
    if not session.canAddInput_(input_obj):
        print("❌ Cannot add input")
        return False
    session.addInput_(input_obj)
    
    # 输出
    photo_output = AVFoundation.AVCapturePhotoOutput.alloc().init()
    if not session.canAddOutput_(photo_output):
        print("❌ Cannot add output")
        return False
    session.addOutput_(photo_output)
    
    # 启动session
    session.startRunning()
    time.sleep(0.5)  # 等摄像头就绪
    
    # 拍照
    delegate = PhotoCaptureDelegate.alloc().init()
    settings = AVFoundation.AVCapturePhotoSettings.alloc().init()
    photo_output.capturePhotoWithDelegate_(settings, delegate)
    
    # 等待拍照完成
    if not delegate.captured.wait(timeout=5):
        print("❌ Capture timeout")
        session.stopRunning()
        return False
    
    session.stopRunning()
    
    # 保存
    if delegate.image_data:
        with open(output_path, 'wb') as f:
            f.write(delegate.image_data.bytes())
        print(f"✅ Photo saved: {output_path} ({os.path.getsize(output_path)} bytes)")
        return True
    else:
        print("❌ No image data")
        return False

if __name__ == '__main__':
    output = sys.argv[1] if len(sys.argv) > 1 else "/tmp/cam_capture.jpg"
    ok = capture_photo(output)
    sys.exit(0 if ok else 1)
