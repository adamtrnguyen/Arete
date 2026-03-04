import * as path from 'path';
import { AretePluginSettings } from '@/domain/settings';

export interface PythonCommand {
	cmd: string;
	args: string[];
	env: Record<string, string | undefined>;
	cwd: string;
}

/**
 * Resolves the Python executable, arguments, environment, and working directory
 * from plugin settings. Shared across AreteClient, CheckService, ServerManager,
 * and SyncService to eliminate duplicated spawn setup logic.
 */
export function resolvePythonCommand(
	settings: AretePluginSettings,
	fallbackCwd?: string,
): PythonCommand {
	const pythonSetting = settings.python_path || 'python3';
	const scriptPath = settings.arete_script_path || '';
	const projectRoot = settings.project_root || '';

	const parts = pythonSetting.split(' ').filter((p) => p.trim() !== '');
	const cmd = parts[0];
	const args = parts.slice(1);
	const env: Record<string, string | undefined> = Object.assign({}, process.env);

	// Derive PYTHONPATH from .py script path
	if (scriptPath && scriptPath.endsWith('.py')) {
		const scriptDir = path.dirname(scriptPath);
		const packageRoot = path.dirname(scriptDir);
		env['PYTHONPATH'] = packageRoot;
		args.push('-m', 'arete');
	} else {
		// Add -m arete unless already present
		const hasArete = args.some((a) => a.toLowerCase().includes('arete'));
		const hasModule = args.includes('-m');
		if (!hasArete && !hasModule) {
			args.push('-m', 'arete');
		}
	}

	// Auto-detect 'src' folder in project root for PYTHONPATH
	const cwd = projectRoot || fallbackCwd || '.';
	if (cwd && cwd !== '.') {
		const srcPath = path.join(cwd, 'src');
		const currentPath = env['PYTHONPATH'] || '';
		env['PYTHONPATH'] = currentPath ? `${currentPath}:${srcPath}` : srcPath;
	}

	// Fix PATH on macOS for GUI apps (Obsidian often lacks .local/bin)
	if (process.platform === 'darwin' && process.env.HOME) {
		const home = process.env.HOME;
		const extraPaths = [
			path.join(home, '.local', 'bin'),
			path.join(home, '.cargo', 'bin'),
			'/opt/homebrew/bin',
			'/usr/local/bin',
		];
		const currentPath = env['PATH'] || '';
		env['PATH'] = `${currentPath}:${extraPaths.join(':')}`;
	}

	return { cmd, args, env, cwd };
}
