import { createContext, useContext, useReducer } from 'react'

const CRMContext = createContext()

const initialState = {
  contacts: [
    { id: 1, name: 'Alice Johnson', email: 'alice@example.com', phone: '555-0101', company: 'Acme Corp', status: 'active' },
    { id: 2, name: 'Bob Smith', email: 'bob@example.com', phone: '555-0102', company: 'Globex Inc', status: 'active' },
    { id: 3, name: 'Carol White', email: 'carol@example.com', phone: '555-0103', company: 'Initech', status: 'lead' },
  ],
  deals: [
    { id: 1, title: 'Enterprise License', contactId: 1, value: 50000, stage: 'proposal', createdAt: '2026-03-15' },
    { id: 2, title: 'Consulting Package', contactId: 2, value: 25000, stage: 'negotiation', createdAt: '2026-04-01' },
    { id: 3, title: 'Starter Plan', contactId: 3, value: 5000, stage: 'qualified', createdAt: '2026-04-10' },
  ],
  nextContactId: 4,
  nextDealId: 4,
}

function crmReducer(state, action) {
  switch (action.type) {
    case 'ADD_CONTACT':
      return {
        ...state,
        contacts: [...state.contacts, { ...action.payload, id: state.nextContactId }],
        nextContactId: state.nextContactId + 1,
      }
    case 'UPDATE_CONTACT':
      return {
        ...state,
        contacts: state.contacts.map(c => c.id === action.payload.id ? action.payload : c),
      }
    case 'DELETE_CONTACT':
      return {
        ...state,
        contacts: state.contacts.filter(c => c.id !== action.payload),
        deals: state.deals.filter(d => d.contactId !== action.payload),
      }
    case 'ADD_DEAL':
      return {
        ...state,
        deals: [...state.deals, { ...action.payload, id: state.nextDealId }],
        nextDealId: state.nextDealId + 1,
      }
    case 'UPDATE_DEAL':
      return {
        ...state,
        deals: state.deals.map(d => d.id === action.payload.id ? action.payload : d),
      }
    case 'DELETE_DEAL':
      return {
        ...state,
        deals: state.deals.filter(d => d.id !== action.payload),
      }
    default:
      return state
  }
}

export function CRMProvider({ children }) {
  const [state, dispatch] = useReducer(crmReducer, initialState)
  return (
    <CRMContext.Provider value={{ state, dispatch }}>
      {children}
    </CRMContext.Provider>
  )
}

export function useCRM() {
  const context = useContext(CRMContext)
  if (!context) {
    throw new Error('useCRM must be used within a CRMProvider')
  }
  return context
}
