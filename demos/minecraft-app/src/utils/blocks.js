export const BlockType = {
  AIR: 0,
  GRASS: 1,
  DIRT: 2,
  STONE: 3,
  WOOD: 4,
  LEAVES: 5,
  SAND: 6,
  WATER: 7,
  COBBLESTONE: 8,
  PLANKS: 9,
  BRICK: 10,
  SNOW: 11,
};

export const BlockColors = {
  [BlockType.GRASS]: '#4a8c2a',
  [BlockType.DIRT]: '#8b6914',
  [BlockType.STONE]: '#808080',
  [BlockType.WOOD]: '#6b4226',
  [BlockType.LEAVES]: '#2d6b1e',
  [BlockType.SAND]: '#d4c36a',
  [BlockType.WATER]: '#3074b5',
  [BlockType.COBBLESTONE]: '#6a6a6a',
  [BlockType.PLANKS]: '#b8945a',
  [BlockType.BRICK]: '#9b4a3c',
  [BlockType.SNOW]: '#f0f0f0',
};

export const BlockNames = {
  [BlockType.GRASS]: 'Grass',
  [BlockType.DIRT]: 'Dirt',
  [BlockType.STONE]: 'Stone',
  [BlockType.WOOD]: 'Wood',
  [BlockType.LEAVES]: 'Leaves',
  [BlockType.SAND]: 'Sand',
  [BlockType.WATER]: 'Water',
  [BlockType.COBBLESTONE]: 'Cobblestone',
  [BlockType.PLANKS]: 'Planks',
  [BlockType.BRICK]: 'Brick',
  [BlockType.SNOW]: 'Snow',
};

export const INVENTORY_BLOCKS = [
  BlockType.GRASS,
  BlockType.DIRT,
  BlockType.STONE,
  BlockType.WOOD,
  BlockType.PLANKS,
  BlockType.COBBLESTONE,
  BlockType.BRICK,
  BlockType.SAND,
  BlockType.SNOW,
];
