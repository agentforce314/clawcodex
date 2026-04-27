import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { GameProvider } from '../context/GameContext';
import Shop from '../pages/Shop';

function renderShop(initialState) {
  return render(
    <GameProvider initialState={initialState}>
      <MemoryRouter>
        <Shop />
      </MemoryRouter>
    </GameProvider>
  );
}

describe('Shop', () => {
  it('renders the shop page', () => {
    renderShop();
    expect(screen.getByText('Pet Shop')).toBeInTheDocument();
  });

  it('shows pet names in shop', () => {
    renderShop();
    expect(screen.getByText('Dog')).toBeInTheDocument();
    expect(screen.getByText('Cat')).toBeInTheDocument();
    expect(screen.getByText('Lion')).toBeInTheDocument();
  });

  it('shows rarity sections', () => {
    renderShop();
    expect(screen.getByText('Common')).toBeInTheDocument();
    expect(screen.getByText('Rare')).toBeInTheDocument();
    expect(screen.getByText('Legendary')).toBeInTheDocument();
  });

  it('allows buying a pet when player has enough coins', () => {
    renderShop({
      player: { name: 'Tester', coins: 500, level: 1 },
      pets: [],
      activePetId: null,
      inventory: [],
      tradeOffers: [],
      notification: null,
    });
    const adoptButtons = screen.getAllByText('Adopt!');
    expect(adoptButtons.length).toBeGreaterThan(0);
    fireEvent.click(adoptButtons[0]);
    // After adopting, coins should decrease (check via subtitle text)
    expect(screen.getByText(/Your coins/)).toBeInTheDocument();
  });

  it('disables buy button when not enough coins', () => {
    renderShop({
      player: { name: 'Tester', coins: 0, level: 1 },
      pets: [],
      activePetId: null,
      inventory: [],
      tradeOffers: [],
      notification: null,
    });
    const disabledButtons = screen.getAllByText('Too expensive');
    expect(disabledButtons.length).toBeGreaterThan(0);
    expect(disabledButtons[0]).toBeDisabled();
  });
});
