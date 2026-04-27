import { createContext, useContext, useReducer, useCallback } from 'react';
import { PET_AGES, getXpForAge, rollPetFromEgg, EGGS } from '../data/pets';

const GameContext = createContext();

const INITIAL_STATE = {
  player: {
    name: 'Player',
    coins: 1000,
    level: 1,
  },
  pets: [],
  activePetId: null,
  inventory: [],
  tradeOffers: [],
  notification: null,
};

let nextPetInstanceId = 1;

function createPetInstance(petData) {
  return {
    instanceId: `pet_${nextPetInstanceId++}`,
    ...petData,
    nickname: petData.name,
    ageIndex: 0,
    xp: 0,
    stats: { hunger: 50, happiness: 50, energy: 50, hygiene: 50, health: 50 },
    adoptedAt: Date.now(),
    taskCooldowns: {},
  };
}

function gameReducer(state, action) {
  switch (action.type) {
    case 'SET_PLAYER_NAME':
      return { ...state, player: { ...state.player, name: action.payload } };

    case 'ADD_COINS':
      return { ...state, player: { ...state.player, coins: state.player.coins + action.payload } };

    case 'SPEND_COINS': {
      if (state.player.coins < action.payload) return state;
      return { ...state, player: { ...state.player, coins: state.player.coins - action.payload } };
    }

    case 'ADOPT_PET': {
      const pet = createPetInstance(action.payload);
      return {
        ...state,
        pets: [...state.pets, pet],
        activePetId: state.activePetId || pet.instanceId,
      };
    }

    case 'SET_ACTIVE_PET':
      return { ...state, activePetId: action.payload };

    case 'RENAME_PET':
      return {
        ...state,
        pets: state.pets.map(p =>
          p.instanceId === action.payload.instanceId
            ? { ...p, nickname: action.payload.nickname }
            : p
        ),
      };

    case 'DO_TASK': {
      const { instanceId, task } = action.payload;
      const now = Date.now();
      return {
        ...state,
        pets: state.pets.map(p => {
          if (p.instanceId !== instanceId) return p;
          const cooldownEnd = p.taskCooldowns[task.id] || 0;
          if (now < cooldownEnd) return p;
          const newStats = { ...p.stats, [task.stat]: Math.min(100, p.stats[task.stat] + task.gain) };
          let newXp = p.xp + task.xp;
          let newAgeIndex = p.ageIndex;
          const xpNeeded = getXpForAge(p.ageIndex);
          if (newXp >= xpNeeded && newAgeIndex < PET_AGES.length - 1) {
            newXp -= xpNeeded;
            newAgeIndex += 1;
          }
          return {
            ...p,
            stats: newStats,
            xp: newXp,
            ageIndex: newAgeIndex,
            taskCooldowns: { ...p.taskCooldowns, [task.id]: now + task.cooldown },
          };
        }),
        player: { ...state.player, coins: state.player.coins + 5 },
      };
    }

    case 'RELEASE_PET': {
      const newPets = state.pets.filter(p => p.instanceId !== action.payload);
      return {
        ...state,
        pets: newPets,
        activePetId: state.activePetId === action.payload
          ? (newPets[0]?.instanceId || null)
          : state.activePetId,
      };
    }

    case 'HATCH_EGG': {
      const egg = EGGS.find(e => e.id === action.payload);
      if (!egg || state.player.coins < egg.price) return state;
      const petData = rollPetFromEgg(egg);
      const pet = createPetInstance(petData);
      return {
        ...state,
        player: { ...state.player, coins: state.player.coins - egg.price },
        pets: [...state.pets, pet],
        activePetId: state.activePetId || pet.instanceId,
        notification: { type: 'hatch', pet, egg },
      };
    }

    case 'CLEAR_NOTIFICATION':
      return { ...state, notification: null };

    case 'COMPLETE_TRADE': {
      const { give, receive } = action.payload;
      const newPets = state.pets.filter(p => !give.includes(p.instanceId));
      receive.forEach(petData => {
        newPets.push(createPetInstance(petData));
      });
      return {
        ...state,
        pets: newPets,
        activePetId: newPets[0]?.instanceId || null,
      };
    }

    default:
      return state;
  }
}

export function GameProvider({ children, initialState }) {
  const [state, dispatch] = useReducer(gameReducer, initialState || INITIAL_STATE);

  const addCoins = useCallback((amount) => dispatch({ type: 'ADD_COINS', payload: amount }), []);
  const spendCoins = useCallback((amount) => dispatch({ type: 'SPEND_COINS', payload: amount }), []);
  const adoptPet = useCallback((petData) => dispatch({ type: 'ADOPT_PET', payload: petData }), []);
  const setActivePet = useCallback((id) => dispatch({ type: 'SET_ACTIVE_PET', payload: id }), []);
  const renamePet = useCallback((instanceId, nickname) =>
    dispatch({ type: 'RENAME_PET', payload: { instanceId, nickname } }), []);
  const doTask = useCallback((instanceId, task) =>
    dispatch({ type: 'DO_TASK', payload: { instanceId, task } }), []);
  const releasePet = useCallback((id) => dispatch({ type: 'RELEASE_PET', payload: id }), []);
  const hatchEgg = useCallback((eggId) => dispatch({ type: 'HATCH_EGG', payload: eggId }), []);
  const clearNotification = useCallback(() => dispatch({ type: 'CLEAR_NOTIFICATION' }), []);
  const setPlayerName = useCallback((name) => dispatch({ type: 'SET_PLAYER_NAME', payload: name }), []);

  const value = {
    state,
    dispatch,
    addCoins,
    spendCoins,
    adoptPet,
    setActivePet,
    renamePet,
    doTask,
    releasePet,
    hatchEgg,
    clearNotification,
    setPlayerName,
  };

  return <GameContext.Provider value={value}>{children}</GameContext.Provider>;
}

export function useGame() {
  const ctx = useContext(GameContext);
  if (!ctx) throw new Error('useGame must be used within GameProvider');
  return ctx;
}
