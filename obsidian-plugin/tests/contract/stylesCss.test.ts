// styles.css is build output of src/styles; it must equal a fresh production build (rule S5).
// tests/test-setup.ts mocks fs and child_process; esbuild's JS API spawns its binary, so both are
// unmocked here (rule Q3).
jest.unmock('child_process');
jest.unmock('fs');
import * as fs from 'fs';
import * as path from 'path';
import * as esbuild from 'esbuild';

const root = path.resolve(__dirname, '../..');

describe('styles.css', () => {
	test('equals a production build of src/styles/index.css', async () => {
		const res = await esbuild.build({
			absWorkingDir: root,
			entryPoints: { styles: 'src/styles/index.css' },
			bundle: true,
			minify: true, // esbuild.config.mjs: minify: prod
			target: 'es2018',
			outdir: path.join(root, 'never-written'),
			write: false,
			logLevel: 'silent',
		});
		const built = res.outputFiles.find((f) => f.path.endsWith('.css'))!.text;
		expect(fs.readFileSync(path.join(root, 'styles.css'), 'utf8')).toBe(built);
	});
});
