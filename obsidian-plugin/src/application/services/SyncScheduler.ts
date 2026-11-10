/** One `arete sync` run: the whole vault (`targetPath` null) or one file. */
export interface SyncRequest {
	prune: boolean;
	targetPath: string | null;
	force: boolean;
}

/** What the scheduler needs from the plugin. */
export interface SyncSchedulerHost {
	/** Run one sync; resolve once its process has exited. */
	run(request: SyncRequest): Promise<void>;
	/** A manual sync was asked for while one runs; nothing was started. */
	busy(): void;
	/** A follow-up sync was queued, or the queued one started. */
	queueChanged(): void;
}

/**
 * Runs one sync at a time: two at once would each create every new card.
 *
 * A manual sync asked for while one runs is refused through `host.busy`. A save is never
 * lost: the saves made while a sync runs become one follow-up, run when it finishes. The
 * follow-up syncs the saved file when there is one, and the whole vault when several were
 * saved: `arete sync` takes one path, and a vault sync sends only what changed, so one
 * process covers every saved file and none is dropped.
 */
export class SyncScheduler {
	private running = false;
	private savedFiles = new Set<string>();
	private closed = false;

	constructor(private host: SyncSchedulerHost) {}

	get isRunning(): boolean {
		return this.running;
	}

	/** Whether a follow-up sync waits for the running one. */
	get isQueued(): boolean {
		return this.savedFiles.size > 0;
	}

	/** A sync the user asked for. Resolves true once it ran, false when it was refused. */
	async requestManual(request: SyncRequest): Promise<boolean> {
		if (this.closed) return false;
		if (this.running) {
			this.host.busy();
			return false;
		}
		await this.drain(request);
		return true;
	}

	/** A sync for a saved file: now when idle, otherwise in the follow-up. */
	async requestSave(filePath: string): Promise<void> {
		if (this.closed) return;
		if (this.running) {
			this.savedFiles.add(filePath);
			this.host.queueChanged();
			return;
		}
		await this.drain({ prune: false, targetPath: filePath, force: false });
	}

	/** Drop the follow-up and refuse new requests. For plugin unload. */
	dispose(): void {
		this.closed = true;
		this.savedFiles.clear();
	}

	private async drain(first: SyncRequest): Promise<void> {
		this.running = true;
		try {
			let next: SyncRequest | null = first;
			while (next && !this.closed) {
				try {
					await this.host.run(next);
				} catch (e) {
					console.error('[Arete] Sync run failed', e);
				}
				next = this.takeFollowUp();
			}
		} finally {
			this.running = false;
		}
	}

	private takeFollowUp(): SyncRequest | null {
		if (this.closed || this.savedFiles.size === 0) return null;
		const files = [...this.savedFiles];
		this.savedFiles.clear();
		this.host.queueChanged();
		return { prune: false, targetPath: files.length === 1 ? files[0] : null, force: false };
	}
}
