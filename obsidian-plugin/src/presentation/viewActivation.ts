import { App, Workspace, WorkspaceLeaf } from 'obsidian';
import { CardYamlEditorView, YAML_EDITOR_VIEW_TYPE } from '@presentation/views/CardYamlEditorView';
import { LocalGraphView, LOCAL_GRAPH_VIEW_TYPE } from '@presentation/views/LocalGraphView';

export async function activateSidebarView(
	workspace: Workspace,
	viewType: string,
): Promise<WorkspaceLeaf | undefined> {
	let leaf = workspace.getLeavesOfType(viewType)[0];
	if (!leaf) {
		const rightLeaf = workspace.getRightLeaf(false);
		if (rightLeaf) {
			await rightLeaf.setViewState({ type: viewType, active: true });
		}
		leaf = workspace.getLeavesOfType(viewType)[0];
	}
	if (leaf) {
		await workspace.revealLeaf(leaf);
	}
	return leaf;
}

export async function activateTabView(
	workspace: Workspace,
	viewType: string,
): Promise<WorkspaceLeaf> {
	let leaf = workspace.getLeavesOfType(viewType)[0];
	if (!leaf) {
		leaf = workspace.getLeaf('tab');
		await leaf.setViewState({ type: viewType, active: true });
	}
	await workspace.revealLeaf(leaf);
	return leaf;
}

export function syncYamlEditorToCard(workspace: Workspace, cardIndex: number): void {
	const leaf = workspace.getLeavesOfType(YAML_EDITOR_VIEW_TYPE)[0];
	if (leaf) {
		const view = leaf.view as CardYamlEditorView;
		if (view?.focusCard) {
			view.focusCard(cardIndex);
		}
	}
}

export async function activateLocalGraph(
	app: App,
	host: {
		getCardContext(filePath: string): string | undefined;
		setCardContext(filePath: string, cardId: string): void;
	},
	cardId?: string,
): Promise<void> {
	const leaf = await activateSidebarView(app.workspace, LOCAL_GRAPH_VIEW_TYPE);
	if (!leaf) return;

	const activeFile = app.workspace.getActiveFile();
	if (leaf.view instanceof LocalGraphView) {
		const view = leaf.view;
		if (cardId) {
			if (activeFile) {
				void view.setContext(activeFile.path, cardId);
				host.setCardContext(activeFile.path, cardId);
			}
		} else if (activeFile) {
			void view.setContext(activeFile.path, host.getCardContext(activeFile.path));
		}
	}
}
