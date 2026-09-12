import { describe, expect, it } from "vitest";

import { ScenarioDialogState } from "./scenarioDialogState";

describe("scenario dialog save lifecycle", () => {
  it("preserves the submitted request identity when a close event arrives during a deferred save", async () => {
    const state = new ScenarioDialogState();
    const requestId = "f6e942dc-faf7-4db6-b332-928ad2cb86e3";
    let resolveCreate!: (value: { id: string }) => void;
    const create = new Promise<{ id: string }>((resolve) => { resolveCreate = resolve; });
    const operation = state.beginSubmission(() => requestId);

    // This is the same interaction as pressing Cancel or the modal close
    // control while the create handler awaits the WFS response.
    expect(state.close()).toBe(false);
    resolveCreate({ id: operation.requestId });

    const saved = await create;
    const ownedRequestIds: Array<string | null> = [];
    ownedRequestIds.push(saved.id);
    state.finishSubmission({ needsReconciliation: true });

    expect(ownedRequestIds).toEqual([requestId]);
    expect(state.close()).toBe(true);
  });

  it("uses the retained request identifier for a safe retry after an uncertain save", () => {
    const state = new ScenarioDialogState();
    const requestId = "f6e942dc-faf7-4db6-b332-928ad2cb86e3";
    const first = state.beginSubmission(() => requestId);
    state.finishSubmission({ needsReconciliation: true });
    const retry = state.beginSubmission(() => "123e4567-e89b-12d3-a456-426614174000");

    expect(first).toEqual({ requestId, reconcile: false });
    expect(retry).toEqual({ requestId, reconcile: true });
  });
});
