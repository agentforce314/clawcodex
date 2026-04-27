import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import App from '../App';

describe('App', () => {
  it('renders the navbar with brand name', () => {
    render(<App />);
    expect(screen.getByText('Adopt Me!')).toBeInTheDocument();
  });

  it('renders navigation links', () => {
    render(<App />);
    const nav = screen.getByRole('navigation');
    expect(nav).toHaveTextContent('Home');
    expect(nav).toHaveTextContent('Nursery');
    expect(nav).toHaveTextContent(/My Pets/);
    expect(nav).toHaveTextContent('Shop');
    expect(nav).toHaveTextContent('Trade');
  });

  it('shows the home page by default', () => {
    render(<App />);
    expect(screen.getByText('Welcome to Adopt Me!')).toBeInTheDocument();
  });

  it('displays initial coin amount', () => {
    render(<App />);
    const coinElements = screen.getAllByText(/1,000/);
    expect(coinElements.length).toBeGreaterThan(0);
  });
});
