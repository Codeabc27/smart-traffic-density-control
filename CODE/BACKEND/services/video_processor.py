import csv, json, threading
from datetime import datetime, timezone
from pathlib import Path
import cv2
from ..config import settings, VEHICLE_CLASSES, APPROACH_ROIS, resolve_model_path
from ..database import database_connection
from .traffic_analyzer import assign_approach, summarize, queue_proxy_for_detections, is_near_queue_zone
from .signal_optimizer import SignalController

_MODEL_CACHE={}
_MODEL_LOCK=threading.Lock()


def get_model():
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise RuntimeError("Ultralytics is not installed. Run: pip install -r requirements.txt") from exc
    path=resolve_model_path()
    if not path.exists():
        raise FileNotFoundError(f"YOLO model not found: {path}. Place yolov8n.pt in MODELS or set MODEL_PATH in .env.")
    key=str(path.resolve())
    with _MODEL_LOCK:
        if key not in _MODEL_CACHE:
            _MODEL_CACHE[key]=YOLO(str(path))
        return _MODEL_CACHE[key]

class VideoProcessor:
    def __init__(self,job_id,input_path,output_path,csv_path,runtime_state,cancel_event,frame_hub=None):
        self.job_id=job_id; self.input_path=input_path; self.output_path=output_path; self.csv_path=csv_path
        self.runtime_state=runtime_state; self.cancel_event=cancel_event; self.frame_hub=frame_hub

    def _persist_runtime(self,db,row):
        db.execute("UPDATE analysis_jobs SET progress=?,frame=?,total_frames=?,runtime_json=?,updated_at=? WHERE id=?",(row.get("progress",0),row.get("frame",0),row.get("total_frames",0),json.dumps(row),datetime.now(timezone.utc).isoformat(),self.job_id))

    def _insert_result(self,db,row):
        db.execute("INSERT INTO traffic_results(job_id,frame,timestamp,car_count,motorcycle_count,bus_count,truck_count,total_count,density,north_count,east_count,south_count,west_count,north_density,east_density,south_density,west_density,selected_phase,signal_state,green_seconds,remaining_seconds,queue_proxy,north_queue,east_queue,south_queue,west_queue) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(
            self.job_id,row["frame"],row["timestamp"],row["car_count"],row["motorcycle_count"],row["bus_count"],row["truck_count"],row["total_count"],row["density"],row["north_count"],row["east_count"],row["south_count"],row["west_count"],row["north_density"],row["east_density"],row["south_density"],row["west_density"],row["selected_phase"],row["signal_state"],row["green_seconds"],row["remaining_seconds"],row["queue_proxy"],row["north_queue"],row["east_queue"],row["south_queue"],row["west_queue"]))

    def run(self):
        cap=None; writer=None
        try:
            model=get_model()
            cap=cv2.VideoCapture(str(self.input_path))
            if not cap.isOpened(): raise RuntimeError("Unable to open the uploaded video.")
            fps=cap.get(cv2.CAP_PROP_FPS) or 25.0
            width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0); height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            total_frames=int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if width<=0 or height<=0: raise RuntimeError("The video has invalid dimensions.")
            fourcc=cv2.VideoWriter_fourcc(*"mp4v")
            writer=cv2.VideoWriter(str(self.output_path),fourcc,fps,(width,height))
            if not writer.isOpened(): raise RuntimeError("Unable to create the processed output video. Try an MP4/AVI input with a supported codec.")
            controller=SignalController(); rows=[]; frame_index=0; previous_time=0.0
            with database_connection() as db:
                db.execute("UPDATE analysis_jobs SET status='running',started_at=datetime('now'),updated_at=datetime('now') WHERE id=?",(self.job_id,))
            while True:
                if self.cancel_event.is_set(): break
                ok,frame=cap.read()
                if not ok: break
                video_time=frame_index/fps; delta=max(0,video_time-previous_time); previous_time=video_time
                detections=[]
                result=model.predict(frame,conf=settings.vehicle_confidence,verbose=False)[0]
                boxes=getattr(result,"boxes",None)
                if boxes is not None:
                    for box in boxes:
                        cls_id=int(box.cls[0].item()); class_name=str(model.names.get(cls_id,cls_id)).lower()
                        if class_name not in VEHICLE_CLASSES: continue
                        conf=float(box.conf[0].item()); x1,y1,x2,y2=[int(v) for v in box.xyxy[0].tolist()]
                        cx,cy=(x1+x2)/2,(y1+y2)/2; approach=assign_approach(cx,cy,width,height)
                        near=is_near_queue_zone(cx,cy,approach,width,height)
                        detections.append({"class_name":class_name,"confidence":conf,"x1":x1,"y1":y1,"x2":x2,"y2":y2,"approach":approach,"near_queue_zone":near})
                summary=summarize(detections,width,height); queues=queue_proxy_for_detections(detections,width,height)
                signal=controller.update(summary["approaches"],queues,video_time,delta)

                for name,roi in APPROACH_ROIS.items():
                    ax1,ay1=int(roi["x1"]*width),int(roi["y1"]*height); ax2,ay2=int(roi["x2"]*width),int(roi["y2"]*height)
                    cv2.rectangle(frame,(ax1,ay1),(ax2,ay2),(180,180,180),1); cv2.putText(frame,name,(ax1+8,ay1+22),cv2.FONT_HERSHEY_SIMPLEX,0.55,(230,230,230),2)
                for d in detections:
                    color=(0,220,80) if d["class_name"]=="car" else (255,170,0) if d["class_name"]=="motorcycle" else (0,180,255) if d["class_name"]=="bus" else (180,60,255)
                    cv2.rectangle(frame,(d["x1"],d["y1"]),(d["x2"],d["y2"]),color,2); cv2.putText(frame,f'{d["class_name"]} {d["confidence"]:.2f}',(d["x1"],max(20,d["y1"]-7)),cv2.FONT_HERSHEY_SIMPLEX,0.5,color,2)
                overlay=frame.copy(); cv2.rectangle(overlay,(10,10),(500,125),(15,25,40),-1); frame=cv2.addWeighted(overlay,0.76,frame,0.24,0)
                cv2.putText(frame,f'PHASE: {signal["phase"] or "-"}  {signal["state"]}',(20,38),cv2.FONT_HERSHEY_SIMPLEX,0.7,(255,255,255),2)
                cv2.putText(frame,f'VEHICLES IN FRAME: {summary["total"]}  DENSITY: {summary["density"]}',(20,66),cv2.FONT_HERSHEY_SIMPLEX,0.55,(255,255,255),2)
                cv2.putText(frame,f'GREEN: {signal["green_seconds"]:.0f}s  REMAINING: {signal["remaining_seconds"]:.0f}s',(20,92),cv2.FONT_HERSHEY_SIMPLEX,0.55,(220,235,255),2)
                cv2.putText(frame,'QUEUE = ESTIMATED OCCUPANCY PROXY',(20,115),cv2.FONT_HERSHEY_SIMPLEX,0.42,(210,210,210),1)
                writer.write(frame)
                if self.frame_hub is not None:
                    self.frame_hub.publish(self.job_id, frame)
                row={"frame":frame_index,"timestamp":round(video_time,3),"car_count":summary["types"]["car"],"motorcycle_count":summary["types"]["motorcycle"],"bus_count":summary["types"]["bus"],"truck_count":summary["types"]["truck"],"total_count":summary["total"],"density":summary["density"],"north_count":summary["approaches"]["NORTH"],"east_count":summary["approaches"]["EAST"],"south_count":summary["approaches"]["SOUTH"],"west_count":summary["approaches"]["WEST"],"north_density":summary["density_ratio"]["NORTH"],"east_density":summary["density_ratio"]["EAST"],"south_density":summary["density_ratio"]["SOUTH"],"west_density":summary["density_ratio"]["WEST"],"selected_phase":signal["phase"],"signal_state":signal["state"],"green_seconds":signal["green_seconds"],"remaining_seconds":signal["remaining_seconds"],"queue_proxy":max(queues.values()) if queues else 0,"north_queue":queues["NORTH"],"east_queue":queues["EAST"],"south_queue":queues["SOUTH"],"west_queue":queues["WEST"]}
                rows.append(row)
                progress=(frame_index+1)/total_frames if total_frames else 0
                self.runtime_state.update({"status":"running","progress":round(progress,4),"frame":frame_index+1,"total_frames":total_frames,"summary":summary,"signal":signal,"updated_at":video_time})
                if frame_index % settings.db_sample_every_frames == 0:
                    with database_connection() as db: self._insert_result(db,row)
                if frame_index % settings.runtime_save_every_frames == 0:
                    with database_connection() as db: self._persist_runtime(db,self.runtime_state)
                frame_index+=1

            if self.cancel_event.is_set():
                self.runtime_state["status"]="cancelled"
                with database_connection() as db:
                    db.execute("UPDATE analysis_jobs SET status='cancelled',completed_at=datetime('now'),progress=?,runtime_json=?,updated_at=datetime('now') WHERE id=?",(self.runtime_state.get("progress",0),json.dumps(self.runtime_state),self.job_id))
                return
            if not rows:
                raise RuntimeError("No readable frames were found in the video.")
            # Ensure final frame is persisted when sampling interval does not align.
            with database_connection() as db:
                existing=db.execute("SELECT MAX(frame) AS frame FROM traffic_results WHERE job_id=?",(self.job_id,)).fetchone()["frame"]
                final_row=rows[-1]
                if existing != final_row["frame"]: self._insert_result(db,final_row)
            with self.csv_path.open("w",newline="",encoding="utf-8") as f:
                writer_csv=csv.DictWriter(f,fieldnames=list(rows[0].keys())); writer_csv.writeheader(); writer_csv.writerows(rows)
            self.runtime_state.update({"status":"completed","progress":1.0,"frame":frame_index,"total_frames":total_frames})
            with database_connection() as db:
                self._persist_runtime(db,self.runtime_state)
                db.execute("UPDATE analysis_jobs SET status='completed',output_video=?,result_csv=?,completed_at=datetime('now'),progress=1.0,frame=?,total_frames=?,runtime_json=?,updated_at=datetime('now') WHERE id=?",(str(self.output_path),str(self.csv_path),frame_index,total_frames,json.dumps(self.runtime_state),self.job_id))
        finally:
            if cap is not None: cap.release()
            if writer is not None: writer.release()
