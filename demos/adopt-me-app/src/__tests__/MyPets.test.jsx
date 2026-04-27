import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { GameProvider } from '../context/GameContext';
import MyPets from '../pages/MyPets';

const petInstance = {
  instanceId: 'pet_test_1',
  id: 'dog',
  name: 'Dog',
  emoji: '🐶',
  rarity: 'COMMON',
  basePrice: 100,
  nickname: 'Buddy',
  ageIndex: 0,
  xp: 0,
  stats: { hunger: 50, happiness: 50, energy: 50, hygiene: 50, health: 50 },
  adoptedAt: Date.now(),
  taskCooldowns: {},
};

function renderMyPets(overrides = {}) {
  const state = {
    player: { name: 'Tester', coins: 500, level: 1 },
    pets: [petInstance],
    activePetId: 'pet_test_1',
    inventory: [],
    tradeOffers: [],
    notification: null,
    ...overrides,
  };
  return render(
    <GameProvider initialState={state}>
      <MemoryRouter>
        <MyPets />
      </MemoryRouter>
    </GameProvider>
  );
}

describe('MyPets', () => {
  it('shows empty state when no pets', () => {
    renderMyPets({ pets: [], activePetId: null });
    expect(screen.getByText('No pets yet!')).toBeInTheDocument();
  });

  it('shows pet collection with count', () => {
    renderMyPets();
    expect(screen.getByText('Your Collection (1)')).toBeInTheDocument();
  });

  it('shows active pet detail', () => {
    renderMyPets();
    const buddyElements = screen.getAllByText('Buddy');
    expect(buddyElements.length).toBe(2);
    expect(screen.getByText(/Newborn/)).toBeInTheDocument();
  });

  it('shows care task buttons', () => {
    renderMyPets();
    expect(screen.getByText('Feed')).toBeInTheDocument();
    expect(screen.getByText('Play')).toBeInTheDocument();
    expect(screen.getByText('Sleep')).toBeInTheDocument();
    expect(screen.getByText('Clean')).toBeInTheDocument();
    expect(screen.getByText('Heal')).toBeInTheDocument();
  });

  it('executes a task and shows XP reward', () => {
    renderMyPets();
    const feedBtn = screen.getByText('Feed').closest('button');
    fireEvent.click(feedBtn);
    const xpElements = screen.getAllByText(/XP/);
    expect(xpElements.length).toBeGreaterThan(0);
  });

  it('shows release pet button', () => {
    renderMyPets();
    expect(screen.getByText('Release Pet')).toBeInTheDocument();
  });

  it('shows confirmation before releasing', () => {
    renderMyPets();
    fireEvent.click(screen.getByText('Release Pet'));
    expect(screen.getByText(/Are you sure/)).toBeInTheDocument();
    expect(screen.getByText('Yes, Release')).toBeInTheDocument();
  });

  it('shows rename button', () => {
    renderMyPets();
    expect(screen.getByLabelText('Rename pet')).toBeInTheDocument();
  });
});
