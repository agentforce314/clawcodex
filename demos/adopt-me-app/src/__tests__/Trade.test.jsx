import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { GameProvider } from '../context/GameContext';
import Trade from '../pages/Trade';

function renderTrade(initialState) {
  return render(
    <GameProvider initialState={initialState}>
      <MemoryRouter>
        <Trade />
      </MemoryRouter>
    </GameProvider>
  );
}

describe('Trade', () => {
  it('renders the trade page', () => {
    renderTrade();
    expect(screen.getByText('Trading Plaza')).toBeInTheDocument();
  });

  it('shows NPC traders', () => {
    renderTrade();
    expect(screen.getByText('Friendly Farmer')).toBeInTheDocument();
    expect(screen.getByText('Explorer Emma')).toBeInTheDocument();
    expect(screen.getByText('Royal Knight')).toBeInTheDocument();
    expect(screen.getByText('Mystic Mage')).toBeInTheDocument();
  });

  it('shows empty state when no pets', () => {
    renderTrade({
      player: { name: 'Tester', coins: 500, level: 1 },
      pets: [],
      activePetId: null,
      inventory: [],
      tradeOffers: [],
      notification: null,
    });
    expect(screen.getByText('No pets to trade')).toBeInTheDocument();
  });

  it('shows pet selection when pets exist', () => {
    renderTrade({
      player: { name: 'Tester', coins: 500, level: 1 },
      pets: [{
        instanceId: 'pet_1',
        id: 'dog',
        name: 'Dog',
        emoji: '🐶',
        rarity: 'COMMON',
        basePrice: 100,
        nickname: 'Rex',
        ageIndex: 0,
        xp: 0,
        stats: { hunger: 50, happiness: 50, energy: 50, hygiene: 50, health: 50 },
        adoptedAt: Date.now(),
        taskCooldowns: {},
      }],
      activePetId: 'pet_1',
      inventory: [],
      tradeOffers: [],
      notification: null,
    });
    expect(screen.getByText('Select a Pet to Trade')).toBeInTheDocument();
    expect(screen.getByText('Rex')).toBeInTheDocument();
  });
});
