"""
Bayesian Trust Engine
Tracks historical reliability to defend against sleeper agent degradation.
"""
from clasp.vendor.silex.storage.database import Database
from clasp.vendor.silex.utils.logger import setup_logger
from datetime import datetime, timezone

log = setup_logger("silex.security.trust")

class BayesianTrustEngine:
    def __init__(self, db: Database, cutoff: float = 0.50, floor: float = 0.30):
        self.db = db
        self.cutoff = cutoff
        self.floor = floor

    async def initialize(self):
        """Load or create trust state."""
        row = await self.db.fetch_one("SELECT * FROM trust_state WHERE actor_id = 'kronos'")
        if not row:
            now = datetime.now(timezone.utc).timestamp()
            await self.db.execute(
                "INSERT INTO trust_state (actor_id, alpha, beta, last_updated) VALUES (?, ?, ?, ?)",
                ('kronos', 10.0, 1.0, now)
            )

    async def record_operation(self, success: bool, is_security_violation: bool = False):
        """Update Bayesian trust model."""
        row = await self.db.fetch_one("SELECT alpha, beta FROM trust_state WHERE actor_id = 'kronos'")
        if not row:
            await self.initialize()
            row = await self.db.fetch_one("SELECT alpha, beta FROM trust_state WHERE actor_id = 'kronos'")
            if not row:
                return
            
        alpha, beta = row["alpha"], row["beta"]
        
        if success:
            alpha += 1.0
        else:
            weight = 4.0 if is_security_violation else 1.0
            beta += weight
            
        now = datetime.now(timezone.utc).timestamp()
        await self.db.execute(
            "UPDATE trust_state SET alpha = ?, beta = ?, last_updated = ? WHERE actor_id = 'kronos'",
            (alpha, beta, now)
        )
        
        trust_score = alpha / (alpha + beta)
        if trust_score < self.floor:
            log.error(f"TRUST FLOOR BREACHED ({trust_score:.2f}). System requires intervention.")
        elif trust_score < self.cutoff:
            log.warning(f"TRUST CUTOFF BREACHED ({trust_score:.2f}). Destructive tools locked.")

    async def verify_actor_threshold(self) -> bool:
        """Check if current trust score allows sensitive operations."""
        row = await self.db.fetch_one("SELECT alpha, beta FROM trust_state WHERE actor_id = 'kronos'")
        if not row:
            return True
            
        alpha, beta = row["alpha"], row["beta"]
        trust_score = alpha / (alpha + beta)
        
        return trust_score >= self.cutoff
        
    async def get_trust_score(self) -> float:
        row = await self.db.fetch_one("SELECT alpha, beta FROM trust_state WHERE actor_id = 'kronos'")
        if not row:
            return 1.0
            
        alpha, beta = row["alpha"], row["beta"]
        return alpha / (alpha + beta)
