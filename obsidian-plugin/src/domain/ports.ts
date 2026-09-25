/**
 * Ports: what application services need from the Arete backend. AreteClient
 * (infrastructure) implements them; services never import infrastructure (PL3).
 */

export interface AnkiModelSource {
	modelStyling(modelName: string): Promise<string>;
	modelTemplates(modelName: string): Promise<Record<string, { Front: string; Back: string }>>;
}

export interface CardStatsSource {
	/** Raw per-card stats rows from Python (snake_case); the service maps them. */
	getCardStats(nids: number[]): Promise<unknown>;
}

export interface CardSuspender {
	suspendCards(cardIds: number[]): Promise<boolean>;
	unsuspendCards(cardIds: number[]): Promise<boolean>;
}

export interface FileChecker {
	/** `arete vault check` result for one file (absolute path). */
	getCheckResult(filePath: string): Promise<any>;
}
