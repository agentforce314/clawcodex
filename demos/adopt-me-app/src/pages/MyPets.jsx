import { useState } from 'react';
import { useGame } from '../context/GameContext';
import PetCard from '../components/PetCard';
import StatBar from '../components/StatBar';
import { TASKS, PET_AGES, getXpForAge } from '../data/pets';

export default function MyPets() {
  const { state, setActivePet, doTask, renamePet, releasePet } = useGame();
  const [renaming, setRenaming] = useState(null);
  const [newName, setNewName] = useState('');
  const [confirmRelease, setConfirmRelease] = useState(null);

  const activePet = state.pets.find(p => p.instanceId === state.activePetId);

  if (state.pets.length === 0) {
    return (
      <div className="page my-pets-page">
        <h1>My Pets</h1>
        <div className="empty-state">
          <span className="empty-state__emoji">🐾</span>
          <h2>No pets yet!</h2>
          <p>Visit the Nursery or Shop to adopt your first pet.</p>
        </div>
      </div>
    );
  }

  const now = Date.now();

  function startRename(pet) {
    setRenaming(pet.instanceId);
    setNewName(pet.nickname);
  }

  function submitRename(instanceId) {
    if (newName.trim()) {
      renamePet(instanceId, newName.trim());
    }
    setRenaming(null);
  }

  function handleRelease(instanceId) {
    releasePet(instanceId);
    setConfirmRelease(null);
  }

  return (
    <div className="page my-pets-page">
      <h1>My Pets</h1>

      <div className="my-pets-layout">
        <div className="pet-list">
          <h2>Your Collection ({state.pets.length})</h2>
          {state.pets.map(pet => (
            <PetCard
              key={pet.instanceId}
              pet={pet}
              selected={pet.instanceId === state.activePetId}
              compact
              onClick={() => setActivePet(pet.instanceId)}
            />
          ))}
        </div>

        {activePet && (
          <div className="pet-detail">
            <div className="pet-detail__header">
              <span className="pet-detail__emoji">{activePet.emoji}</span>
              <div>
                {renaming === activePet.instanceId ? (
                  <div className="rename-form">
                    <input
                      value={newName}
                      onChange={e => setNewName(e.target.value)}
                      onKeyDown={e => e.key === 'Enter' && submitRename(activePet.instanceId)}
                      maxLength={20}
                      autoFocus
                      aria-label="Pet nickname"
                    />
                    <button className="btn btn--small" onClick={() => submitRename(activePet.instanceId)}>Save</button>
                    <button className="btn btn--small btn--ghost" onClick={() => setRenaming(null)}>Cancel</button>
                  </div>
                ) : (
                  <h2>
                    {activePet.nickname}
                    <button className="btn btn--icon" onClick={() => startRename(activePet)} aria-label="Rename pet">✏️</button>
                  </h2>
                )}
                <p>{activePet.name} &middot; {PET_AGES[activePet.ageIndex]}</p>
              </div>
            </div>

            <div className="pet-detail__xp">
              <div className="xp-bar">
                <div className="xp-bar__fill" style={{ width: `${(activePet.xp / getXpForAge(activePet.ageIndex)) * 100}%` }} />
              </div>
              <span>XP: {activePet.xp} / {getXpForAge(activePet.ageIndex)}</span>
            </div>

            <div className="pet-detail__stats">
              <h3>Stats</h3>
              <StatBar label="Hunger 🍖" value={activePet.stats.hunger} />
              <StatBar label="Happiness 😄" value={activePet.stats.happiness} />
              <StatBar label="Energy ⚡" value={activePet.stats.energy} />
              <StatBar label="Hygiene ✨" value={activePet.stats.hygiene} />
              <StatBar label="Health ❤️" value={activePet.stats.health} />
            </div>

            <div className="pet-detail__tasks">
              <h3>Care Tasks</h3>
              <div className="task-grid">
                {TASKS.map(task => {
                  const cooldownEnd = activePet.taskCooldowns[task.id] || 0;
                  const onCooldown = now < cooldownEnd;
                  const cooldownLeft = onCooldown ? Math.ceil((cooldownEnd - now) / 1000) : 0;
                  return (
                    <button
                      key={task.id}
                      className={`task-btn ${onCooldown ? 'task-btn--cooldown' : ''}`}
                      disabled={onCooldown}
                      onClick={() => doTask(activePet.instanceId, task)}
                    >
                      <span className="task-btn__emoji">{task.emoji}</span>
                      <span className="task-btn__name">{task.name}</span>
                      {onCooldown && <span className="task-btn__timer">{cooldownLeft}s</span>}
                      {!onCooldown && <span className="task-btn__reward">+{task.xp} XP</span>}
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="pet-detail__actions">
              {confirmRelease === activePet.instanceId ? (
                <div className="confirm-release">
                  <p>Are you sure you want to release {activePet.nickname}?</p>
                  <button className="btn btn--danger" onClick={() => handleRelease(activePet.instanceId)}>Yes, Release</button>
                  <button className="btn btn--ghost" onClick={() => setConfirmRelease(null)}>Cancel</button>
                </div>
              ) : (
                <button className="btn btn--danger-outline" onClick={() => setConfirmRelease(activePet.instanceId)}>
                  Release Pet
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
