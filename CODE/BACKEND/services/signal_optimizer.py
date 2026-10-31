from dataclasses import dataclass, field
from typing import Dict
from ..config import settings, APPROACHES

@dataclass
class SignalController:
    phase: str|None=None
    state: str="RED"
    green_duration: float=0.0
    state_started_at: float=0.0
    phase_started_at: float=0.0
    remaining_seconds: float=0.0
    waiting: Dict[str,float]=field(default_factory=lambda:{a:0.0 for a in APPROACHES})
    last_reason: str="Initializing adaptive control"
    last_queues: Dict[str,float] = field(default_factory=lambda:{a:0.0 for a in APPROACHES})

    def _density(self,count):
        return min(1.0,count/max(1,settings.traffic_capacity))

    def priority_scores(self,counts,queues):
        scores={}
        for a in APPROACHES:
            density=self._density(counts.get(a,0))
            queue=min(1.0,queues.get(a,0.0))
            wait=min(1.0,self.waiting.get(a,0.0)/60.0)
            scores[a]=(counts.get(a,0)*0.50)+(density*25)+(queue*20)+(wait*20)
        return scores

    def choose_phase(self,counts,queues):
        scores=self.priority_scores(counts,queues)

        # Never immediately re-select the phase that just completed. This prevents
        # one busy approach (for example WEST) from remaining GREEN indefinitely.
        candidates=[a for a in APPROACHES if a != self.phase]
        if not candidates:
            candidates=list(APPROACHES)

        # Hard fairness rule: an approach waiting beyond the threshold gets service
        # priority, but only among approaches that are eligible for the next phase.
        starved=[a for a in candidates if self.waiting.get(a,0) >= settings.fairness_threshold_seconds]
        if starved:
            return max(starved,key=lambda a:(self.waiting[a],scores[a],-APPROACHES.index(a)))

        # Adaptive selection among the other three approaches. Waiting time is
        # included so an approach with lower traffic is still eventually served.
        return max(candidates,key=lambda a:(scores[a],self.waiting.get(a,0),-APPROACHES.index(a)))

    def calculate_green(self,count,queue,wait):
        demand=min(1.0,count/max(1,settings.traffic_capacity))
        value=settings.min_green + demand*20 + min(1.0,queue)*12 + min(1.0,wait/60)*13
        return round(max(settings.min_green,min(settings.max_green,value)),1)

    def update(self,counts,queues,video_time,delta):
        for a in APPROACHES:
            if a==self.phase and self.state=="GREEN":
                self.waiting[a]=max(0.0,self.waiting[a]-delta)
            else:
                self.waiting[a]=min(float(settings.max_wait_seconds),self.waiting[a]+delta)

        if self.phase is None:
            self.phase=self.choose_phase(counts,queues)
            self.green_duration=self.calculate_green(counts.get(self.phase,0),queues.get(self.phase,0),self.waiting[self.phase])
            self.state="GREEN"; self.state_started_at=video_time; self.phase_started_at=video_time
            self.last_reason=self._reason(counts,queues,self.phase)

        self.last_queues=dict(queues)
        elapsed=max(0,video_time-self.state_started_at)
        scores=self.priority_scores(counts,queues)
        best=max(APPROACHES,key=lambda a:(scores[a],a))
        current_score=scores.get(self.phase,0)

        if self.state=="GREEN":
            forced=any(self.waiting[a]>=settings.fairness_threshold_seconds for a in APPROACHES if a!=self.phase)
            early=best!=self.phase and elapsed>=settings.min_green and scores[best]>max(1.0,current_score*1.25)
            if elapsed>=self.green_duration or forced or early:
                self.state="YELLOW"; self.state_started_at=video_time; self.remaining_seconds=float(settings.yellow_time)
        elif self.state=="YELLOW" and elapsed>=settings.yellow_time:
            self.phase=self.choose_phase(counts,queues)
            self.green_duration=self.calculate_green(counts.get(self.phase,0),queues.get(self.phase,0),self.waiting[self.phase])
            self.state="GREEN"; self.state_started_at=video_time; self.phase_started_at=video_time
            self.last_reason=self._reason(counts,queues,self.phase)

        if self.state=="GREEN": self.remaining_seconds=max(0,self.green_duration-(video_time-self.state_started_at))
        elif self.state=="YELLOW": self.remaining_seconds=max(0,settings.yellow_time-(video_time-self.state_started_at))
        else: self.remaining_seconds=0
        return self.snapshot()

    def _reason(self,counts,queues,phase):
        return f"{phase} prioritized from vehicle demand, density, estimated queue and waiting time."

    def snapshot(self):
        signals={a:(self.state if a==self.phase else "RED") for a in APPROACHES}
        return {"phase":self.phase,"state":self.state,"green_seconds":round(self.green_duration,1),"remaining_seconds":round(self.remaining_seconds,1),"signals":signals,"waiting":{a:round(v,1) for a,v in self.waiting.items()},"queues":{a:round(self.last_queues.get(a,0),3) for a in APPROACHES},"reason":self.last_reason}
