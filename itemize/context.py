"""Coverage context.

Who the reader is changes which findings matter. An insured patient who has met
their deductible pays their plan's negotiated rate, not the charge -- so a
"billed at 300x Medicare" finding is nearly irrelevant to them, while it is the
single most useful thing we can tell a self-pay patient.

Without this, the tool shouts its loudest finding at the people it helps least.
Every field defaults to None meaning "not stated", and unknown never silently
becomes a claim.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class Context:
    insured: bool = None                 # None = not stated
    deductible_met: bool = None
    emergency: bool = None               # care was emergency
    out_of_network: bool = None          # provider was out of network
    in_network_facility: bool = None     # ...at an in-network facility
    nonprofit_hospital: bool = None      # billed by a 501(c)(3) hospital
    ground_ambulance: bool = None        # bill includes a ground ambulance ride
    good_faith_estimate: float = None    # dollar amount of a GFE, if given
    state: str = ""
    # "fully_insured" | "self_funded" | None. Gates every state protection:
    # state insurance law generally does not reach self-funded ERISA plans,
    # which cover roughly two-thirds of workers with employer coverage.
    plan_funding: str = None

    @property
    def self_pay(self):
        """True only when the reader positively said they are uninsured."""
        return self.insured is False

    @property
    def pays_full_allowed(self):
        """Reader is exposed to the full allowed amount.

        Self-pay, or insured but pre-deductible: in both cases the reader --
        not the insurer -- is absorbing the charge, so price benchmarks and
        line-level errors land on them directly.
        """
        return self.self_pay or (self.insured is True and self.deductible_met is False)

    @property
    def insurer_is_paying(self):
        """Insured and past the deductible: the plan's rate governs."""
        return self.insured is True and self.deductible_met is True

    @property
    def state_law_applies(self):
        """False only when the reader positively said their plan is self-funded."""
        return self.plan_funding != "self_funded"

    def to_dict(self):
        d = asdict(self)
        d["self_pay"] = self.self_pay
        d["pays_full_allowed"] = self.pays_full_allowed
        d["state_law_applies"] = self.state_law_applies
        return d

    @classmethod
    def from_dict(cls, d):
        if not d:
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})
