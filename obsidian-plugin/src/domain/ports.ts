/**
 * Ports: what application services need from the Arete backend. AreteClient
 * (infrastructure) implements them; services never import infrastructure.
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
}

export interface FileChecker {
	/** `arete vault check` result for one file (absolute path). */
	getCheckResult(filePath: string): Promise<any>;
}

/** One card of a study queue, in study order. */
export interface StudyQueueItem {
	position: number;
	id: string;
	title: string;
	file: string;
	isPrereq: boolean;
}

export interface StudyQueue {
	deck: string;
	dueCount: number;
	totalWithPrereqs: number;
	queue: StudyQueueItem[];
}

export interface AnkiBrowser {
	browse(query: string): Promise<boolean>;
}

export interface StudyQueueClient {
	getDeckNames(): Promise<string[]>;
	buildStudyQueue(deck: string | null, depth: number, maxCards: number): Promise<StudyQueue>;
	createQueueDeck(cardIds: string[]): Promise<boolean>;
}
