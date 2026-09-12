export type ScenarioSubmission = {
  requestId: string;
  reconcile: boolean;
};

/**
 * Keeps the identity of a form submission separate from the dialog's visible
 * lifecycle. In particular, a close event cannot clear an identifier while a
 * write or its required refresh is still completing.
 */
export class ScenarioDialogState {
  private requestId: string | null = null;
  private needsReconciliation = false;
  private submissionInFlight = false;

  beginDialog(): boolean {
    if (this.submissionInFlight) return false;
    this.requestId = null;
    this.needsReconciliation = false;
    return true;
  }

  beginSubmission(createRequestId: () => string): ScenarioSubmission {
    if (this.submissionInFlight) throw new Error("A scenario save is already in progress");
    this.requestId ??= createRequestId();
    this.submissionInFlight = true;
    return { requestId: this.requestId, reconcile: this.needsReconciliation };
  }

  finishSubmission(options: { needsReconciliation?: boolean } = {}): void {
    this.submissionInFlight = false;
    // Once a write may have committed, only a confirmed create/reconciliation
    // followed by a dialog close clears this flag. A failed retry lookup says
    // nothing about whether the original write committed.
    if (options.needsReconciliation) this.needsReconciliation = true;
  }

  close(): boolean {
    if (this.submissionInFlight) return false;
    this.requestId = null;
    this.needsReconciliation = false;
    return true;
  }
}
