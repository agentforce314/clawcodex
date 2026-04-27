export const PET_RARITIES = {
  COMMON: { name: 'Common', color: '#8b8b8b', chance: 0.40 },
  UNCOMMON: { name: 'Uncommon', color: '#4caf50', chance: 0.25 },
  RARE: { name: 'Rare', color: '#2196f3', chance: 0.18 },
  ULTRA_RARE: { name: 'Ultra Rare', color: '#9c27b0', chance: 0.10 },
  LEGENDARY: { name: 'Legendary', color: '#ff9800', chance: 0.05 },
  MYTHIC: { name: 'Mythic', color: '#f44336', chance: 0.02 },
};

export const PET_CATALOG = [
  // Common
  { id: 'dog', name: 'Dog', emoji: '🐶', rarity: 'COMMON', basePrice: 100 },
  { id: 'cat', name: 'Cat', emoji: '🐱', rarity: 'COMMON', basePrice: 100 },
  { id: 'rabbit', name: 'Rabbit', emoji: '🐰', rarity: 'COMMON', basePrice: 150 },
  { id: 'hamster', name: 'Hamster', emoji: '🐹', rarity: 'COMMON', basePrice: 120 },
  // Uncommon
  { id: 'fox', name: 'Fox', emoji: '🦊', rarity: 'UNCOMMON', basePrice: 300 },
  { id: 'panda', name: 'Panda', emoji: '🐼', rarity: 'UNCOMMON', basePrice: 350 },
  { id: 'koala', name: 'Koala', emoji: '🐨', rarity: 'UNCOMMON', basePrice: 320 },
  { id: 'penguin', name: 'Penguin', emoji: '🐧', rarity: 'UNCOMMON', basePrice: 300 },
  // Rare
  { id: 'lion', name: 'Lion', emoji: '🦁', rarity: 'RARE', basePrice: 800 },
  { id: 'elephant', name: 'Elephant', emoji: '🐘', rarity: 'RARE', basePrice: 850 },
  { id: 'giraffe', name: 'Giraffe', emoji: '🦒', rarity: 'RARE', basePrice: 900 },
  // Ultra Rare
  { id: 'unicorn', name: 'Unicorn', emoji: '🦄', rarity: 'ULTRA_RARE', basePrice: 2000 },
  { id: 'dragon_baby', name: 'Baby Dragon', emoji: '🐉', rarity: 'ULTRA_RARE', basePrice: 2500 },
  // Legendary
  { id: 'phoenix', name: 'Phoenix', emoji: '🔥', rarity: 'LEGENDARY', basePrice: 5000 },
  { id: 'frost_dragon', name: 'Frost Dragon', emoji: '🐲', rarity: 'LEGENDARY', basePrice: 6000 },
  // Mythic
  { id: 'shadow_dragon', name: 'Shadow Dragon', emoji: '✨', rarity: 'MYTHIC', basePrice: 15000 },
];

export const PET_AGES = ['Newborn', 'Junior', 'Pre-Teen', 'Teen', 'Post-Teen', 'Full Grown'];

export const TASKS = [
  { id: 'feed', name: 'Feed', emoji: '🍼', stat: 'hunger', gain: 25, xp: 10, cooldown: 30000 },
  { id: 'play', name: 'Play', emoji: '⚽', stat: 'happiness', gain: 20, xp: 15, cooldown: 45000 },
  { id: 'sleep', name: 'Sleep', emoji: '💤', stat: 'energy', gain: 30, xp: 10, cooldown: 60000 },
  { id: 'clean', name: 'Clean', emoji: '🛁', stat: 'hygiene', gain: 25, xp: 10, cooldown: 30000 },
  { id: 'heal', name: 'Heal', emoji: '💊', stat: 'health', gain: 35, xp: 20, cooldown: 90000 },
];

export const EGGS = [
  { id: 'starter_egg', name: 'Starter Egg', emoji: '🥚', price: 350, rarities: ['COMMON', 'UNCOMMON'] },
  { id: 'royal_egg', name: 'Royal Egg', emoji: '👑', price: 1450, rarities: ['UNCOMMON', 'RARE', 'ULTRA_RARE'] },
  { id: 'legendary_egg', name: 'Legendary Egg', emoji: '⭐', price: 5000, rarities: ['RARE', 'ULTRA_RARE', 'LEGENDARY'] },
  { id: 'mythic_egg', name: 'Mythic Egg', emoji: '🌌', price: 12000, rarities: ['ULTRA_RARE', 'LEGENDARY', 'MYTHIC'] },
];

export function getXpForAge(ageIndex) {
  return (ageIndex + 1) * 50;
}

export function rollPetFromEgg(egg) {
  const availablePets = PET_CATALOG.filter(p => egg.rarities.includes(p.rarity));
  const totalChance = availablePets.reduce((sum, p) => sum + PET_RARITIES[p.rarity].chance, 0);
  let roll = Math.random() * totalChance;
  for (const pet of availablePets) {
    roll -= PET_RARITIES[pet.rarity].chance;
    if (roll <= 0) return pet;
  }
  return availablePets[availablePets.length - 1];
}
