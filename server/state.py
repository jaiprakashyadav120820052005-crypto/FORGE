import copy
import threading
import time
import uuid
from typing import Any, Dict, Optional


class SessionData:
    def __init__(self, session_id: str):
        self.session_id: str = session_id
        self.last_active: float = time.time()
        self.stage: str = "pick"  # "pick", "results", "competition", "bom"
        self.survey_step: int = 0
        self.survey_answers: Dict[str, Any] = {}
        self.profile: Dict[str, Any] = {}  # Normalized survey profile (A)
        self.resume: Optional[Dict[str, Any]] = None
        self.career_research: Optional[Dict[str, Any]] = None
        self.pick: Optional[str] = None
        self.idea: Optional[Dict[str, Any]] = None
        self.examples: Optional[list] = None
        self.project: str = "line_follower"
        self.budget: int = 3500
        self.bluetooth: bool = False
        self.live: Dict[str, list] = {}  # {part_id: [offers]}
        self.choice: Dict[str, str] = {}  # {part_id: url}
        self.manual: Dict[str, dict] = {}  # {part_id: {url, name, price}}
        self.res: Optional[Dict[str, Any]] = None  # pipeline.select result
        self.custom: Optional[Dict[str, Any]] = None  # custom/learned/gemini plan
        self.custom_error: Optional[str] = None
        self.suggest: Optional[Dict[str, Any]] = None
        self.bom_origin: Optional[str] = None
        self.gemini_counter: Dict[str, int] = {"n": 0}
        self.competition_selected_by_event: Dict[str, list] = {}
        self.research: Dict[str, Any] = {}
        self.specs: Dict[str, Any] = {}
        self.agent: Dict[str, Any] = {}
        self.opt_open: bool = False
        self.opt_swaps: Dict[str, Any] = {}
        self.sold_out: set = set()                       # part ids the user reported sold out (Pivot A)
        self.sold_out_urls: Dict[str, list] = {}         # {part_id: [listing urls]} sold out at one shop only
        self.pending_replan: Optional[Dict[str, Any]] = None   # proposal waiting for accept / discard (never applied silently)
        self.replan_log: list = []                       # accepted re-plans, each with a snapshot so it can be undone

    def touch(self):
        self.last_active = time.time()

    def get_active_parts(self) -> Dict[str, Any]:
        """Returns the active build parts dictionary (custom plan parts or best rule-checked parts),
        respecting any Project Optimizer swaps via core.kept.apply."""
        from core import kept
        if self.custom and self.custom.get("parts"):
            base = self.custom["parts"]
        elif self.res and self.res.get("best"):
            base = self.res["best"]["parts"]
        else:
            base = {}
        if isinstance(base, list):
            base = kept.keyed_parts(base)
        return kept.apply(base, self.opt_swaps)

    def get_build_label(self) -> str:
        from core.data import PROJECTS
        from core.recommendation_catalog import PROJECT_CATALOG
        if self.custom and self.custom.get("label"):
            return self.custom["label"]
        if self.project in PROJECTS:
            return PROJECTS[self.project]["label"]
        if self.project in PROJECT_CATALOG:
            return PROJECT_CATALOG[self.project]["label"]
        return self.project.replace("_", " ").title()


class SessionStore:
    def __init__(self, ttl_seconds: int = 86400):
        self._sessions: Dict[str, SessionData] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds

    def get_or_create(self, session_id: Optional[str] = None) -> SessionData:
        with self._lock:
            now = time.time()
            # Clean expired
            expired = [sid for sid, s in self._sessions.items() if now - s.last_active > self._ttl]
            for sid in expired:
                del self._sessions[sid]

            if session_id and session_id in self._sessions:
                s = self._sessions[session_id]
                s.touch()
                return s

            new_id = session_id or uuid.uuid4().hex
            s = SessionData(new_id)
            self._sessions[new_id] = s
            return s

    def get(self, session_id: str) -> Optional[SessionData]:
        with self._lock:
            s = self._sessions.get(session_id)
            if s:
                s.touch()
            return s


store = SessionStore()
