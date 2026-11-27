const crypto = require('crypto');
const esbuild = require('esbuild');
const babel = require('@babel/core');

const jestHoistPlugin = require.resolve('babel-plugin-jest-hoist');
const NEEDS_JEST_HOIST = /\bjest\.(?:mock|unmock|enableAutomock|disableAutomock)\s*\(/;

module.exports = {
	getCacheKey(sourceText, sourcePath, options) {
		return crypto
			.createHash('sha256')
			.update(sourceText)
			.update('\0')
			.update(sourcePath)
			.update('\0')
			.update(options.configString || '')
			.update('\0esbuild-jest-v2')
			.digest('hex');
	},

	process(sourceText, sourcePath) {
		const loader = sourcePath.endsWith('.tsx')
			? 'tsx'
			: sourcePath.endsWith('.ts')
				? 'ts'
				: sourcePath.endsWith('.jsx')
					? 'jsx'
					: 'js';

		const transformed = esbuild.transformSync(sourceText, {
			loader,
			format: 'cjs',
			target: 'node22',
			sourcemap: 'inline',
			sourcefile: sourcePath,
		});

		if (!NEEDS_JEST_HOIST.test(sourceText)) {
			return { code: transformed.code };
		}

		const hoisted = babel.transformSync(transformed.code, {
			filename: sourcePath,
			configFile: false,
			babelrc: false,
			inputSourceMap: true,
			sourceMaps: 'inline',
			plugins: [jestHoistPlugin],
		});

		return { code: hoisted?.code ?? transformed.code };
	},
};
