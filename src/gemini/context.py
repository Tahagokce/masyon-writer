"""One step's detailed context. A new instance is created for every Jira step."""
import json
from dataclasses import asdict
from google.genai import types


def content(value):
    return types.Content(role="user", parts=[types.Part(text=json.dumps(value, ensure_ascii=False, separators=(",", ":")))])


class StepContext:
    def __init__(self, step, config, secrets):
        self.initial = content(secrets.object({"currentStep":asdict(step), "baseUrl":config.base_url,
                                              "credentialNames":config.secret_names}))
        self.secrets = secrets
        self.latest_exchange = None
        self.snapshot = None
        self.operations = []
        self.resolver_failure = None

    def observe(self, name, args, result):
        if result.get("mutating") or result.get("transition") or (name == "browser_snapshot" and result.get("isError")):
            self.snapshot = None
        if result.get("snapshot"):
            self.snapshot = {k:result[k] for k in ("snapshot","snapshotScope","snapshotTruncated","hint") if k in result}
        if result.get("executed"):
            self.operations.append(self.secrets.object({"tool":name,
                "target":str(args.get("target", ""))[:120], "page":result.get("activePage"),
                "batchExecuted":result.get("batchExecuted"), "status":"ok"}))
            self.operations = self.operations[-8:]
        # Snapshot lives once in the current context, never in historical replies.
        keep = ("isError","executed","unchanged","denied","needsDecision","resolverStatus","candidates","inventoryCount",
                "error","errorClass","recoverable","evidenceId","evidenceIds","assertion","assertions",
                "transition","checkpointRequired","batchExecuted","remainingActions","results","value","reason","modalState")
        return self.secrets.object({k:result[k] for k in keep if result.get(k) is not None})

    def remember(self, model, replies):
        # Preserve the model Content verbatim, including Gemini 3 thought signatures.
        self.latest_exchange = (model, types.Content(role="user", parts=replies))

    def request(self, state, evidence):
        current = {"state":state.compact(), "verificationEvidence":list(evidence.values()),
                   "recentOperations":self.operations}
        if self.snapshot: current["latestInspection"] = self.snapshot
        if self.resolver_failure: current["resolverFailure"] = self.resolver_failure
        return [self.initial, content(self.secrets.object(current)), *(self.latest_exchange or ())]
