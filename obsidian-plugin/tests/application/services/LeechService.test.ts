import '../../test-setup';
import { LeechService } from '@application/services/LeechService';
import { AreteClient } from '@infrastructure/arete/AreteClient';
import { DEFAULT_SETTINGS } from '@domain/settings';

describe('LeechService', () => {
	let service: LeechService;
	let areteClient: AreteClient;

	beforeEach(() => {
		areteClient = new AreteClient(DEFAULT_SETTINGS);
		service = new LeechService(areteClient);
	});

	test('getLeeches flattens and sorts by lapses', () => {
		const cache: any = {
			concepts: {
				C1: {
					filePath: 'f1.md',
					fileName: 'f1',
					primaryDeck: 'D1',
					problematicCards: [
						{ lapses: 5, cardId: 1 },
						{ lapses: 10, cardId: 2 },
					],
				},
				C2: {
					filePath: 'f2.md',
					fileName: 'f2',
					primaryDeck: 'D2',
					problematicCards: [{ lapses: 2, cardId: 3 }],
				},
			},
		};

		const leeches = service.getLeeches(cache);
		expect(leeches).toHaveLength(3);
		expect(leeches[0].cardId).toBe(2); // Highest lapses (10)
		expect(leeches[1].cardId).toBe(1); // (5)
		expect(leeches[2].cardId).toBe(3); // (2)
		expect(leeches[0].filePath).toBe('f1.md');
		expect(leeches[0].deck).toBe('D1');
	});

	test('suspendCard calls areteClient', async () => {
		areteClient.suspendCards = jest.fn().mockResolvedValue(true);
		const result = await service.suspendCard(123);
		expect(areteClient.suspendCards).toHaveBeenCalledWith([123]);
		expect(result).toBe(true);
	});

	test('unsuspendCard calls areteClient', async () => {
		areteClient.unsuspendCards = jest.fn().mockResolvedValue(true);
		const result = await service.unsuspendCard(123);
		expect(areteClient.unsuspendCards).toHaveBeenCalledWith([123]);
		expect(result).toBe(true);
	});
});
