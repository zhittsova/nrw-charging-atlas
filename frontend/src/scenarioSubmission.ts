import type { ProposedChargerInput } from "./wfsTransactions";
import {
  createScenarioClient,
  UncertainScenarioInsertError,
  type ScenarioCreateResult
} from "./scenarioClient";
import { ScenarioDialogState, type ScenarioSubmission } from "./scenarioDialogState";

type ScenarioClient = ReturnType<typeof createScenarioClient>;

export type StartedScenarioSubmission = {
  operation: ScenarioSubmission;
  saved: ScenarioCreateResult;
};

/**
 * The write portion of the modal submit handler. Keeping it separate from map
 * rendering lets the real handler and recovery tests share one retry contract.
 */
export async function submitScenarioProposal(options: {
  client: ScenarioClient;
  dialogState: ScenarioDialogState;
  createRequestId: () => string;
  input: Omit<ProposedChargerInput, "requestId">;
}): Promise<StartedScenarioSubmission> {
  const operation = options.dialogState.beginSubmission(options.createRequestId);
  try {
    const saved = await options.client.create({
      ...options.input,
      requestId: operation.requestId
    }, { reconcile: operation.reconcile });
    return { operation, saved };
  } catch (error) {
    options.dialogState.finishSubmission({
      needsReconciliation: error instanceof UncertainScenarioInsertError
    });
    throw error;
  }
}
