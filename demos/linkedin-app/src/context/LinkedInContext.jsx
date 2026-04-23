import { createContext, useContext, useState } from 'react'

const LinkedInContext = createContext()

const initialCurrentUser = {
  id: 'user-1',
  name: 'Alex Johnson',
  headline: 'Senior Software Engineer at TechCorp',
  avatar: 'https://i.pravatar.cc/150?u=alex',
  banner: 'https://picsum.photos/seed/alex-banner/800/200',
  location: 'San Francisco, CA',
  connections: 487,
  about: 'Passionate software engineer with 8+ years of experience building scalable web applications. Skilled in React, Node.js, and cloud infrastructure.',
  experience: [
    { title: 'Senior Software Engineer', company: 'TechCorp', duration: '2022 - Present', logo: 'https://i.pravatar.cc/40?u=techcorp' },
    { title: 'Software Engineer', company: 'StartupXYZ', duration: '2019 - 2022', logo: 'https://i.pravatar.cc/40?u=startupxyz' },
    { title: 'Junior Developer', company: 'WebAgency', duration: '2017 - 2019', logo: 'https://i.pravatar.cc/40?u=webagency' },
  ],
}

const initialPeople = [
  { id: 'user-2', name: 'Sarah Chen', headline: 'Product Manager at InnovateCo', avatar: 'https://i.pravatar.cc/150?u=sarah', mutual: 12 },
  { id: 'user-3', name: 'Marcus Williams', headline: 'UX Designer at DesignHub', avatar: 'https://i.pravatar.cc/150?u=marcus', mutual: 8 },
  { id: 'user-4', name: 'Priya Patel', headline: 'Data Scientist at DataFlow', avatar: 'https://i.pravatar.cc/150?u=priya', mutual: 23 },
  { id: 'user-5', name: 'James O\'Brien', headline: 'Engineering Manager at CloudScale', avatar: 'https://i.pravatar.cc/150?u=james', mutual: 5 },
  { id: 'user-6', name: 'Emily Zhang', headline: 'Frontend Developer at PixelPerfect', avatar: 'https://i.pravatar.cc/150?u=emily', mutual: 15 },
  { id: 'user-7', name: 'David Kim', headline: 'CTO at NextGen Startup', avatar: 'https://i.pravatar.cc/150?u=david', mutual: 31 },
  { id: 'user-8', name: 'Rachel Green', headline: 'HR Director at PeopleFirst', avatar: 'https://i.pravatar.cc/150?u=rachel', mutual: 3 },
]

const initialPosts = [
  {
    id: 'post-1',
    author: { name: 'Sarah Chen', headline: 'Product Manager at InnovateCo', avatar: 'https://i.pravatar.cc/150?u=sarah' },
    content: 'Excited to announce that our team just shipped a major product update! After 6 months of hard work, we\'ve completely redesigned the onboarding experience. Early metrics show a 40% improvement in user activation. So proud of this team!',
    timestamp: '2h ago',
    likes: 142,
    comments: 28,
    reposts: 12,
    liked: false,
  },
  {
    id: 'post-2',
    author: { name: 'Marcus Williams', headline: 'UX Designer at DesignHub', avatar: 'https://i.pravatar.cc/150?u=marcus' },
    content: 'Hot take: The best design systems are the ones nobody notices. If users are thinking about your UI instead of their task, something went wrong.\n\nWhat are your thoughts on invisible design? Would love to hear different perspectives.',
    timestamp: '5h ago',
    likes: 89,
    comments: 45,
    reposts: 7,
    liked: false,
  },
  {
    id: 'post-3',
    author: { name: 'Priya Patel', headline: 'Data Scientist at DataFlow', avatar: 'https://i.pravatar.cc/150?u=priya' },
    content: 'Just published my latest article on implementing RAG (Retrieval-Augmented Generation) pipelines in production. Key takeaways:\n\n1. Chunking strategy matters more than model choice\n2. Hybrid search outperforms pure vector search\n3. Evaluation frameworks are non-negotiable\n\nLink in comments!',
    timestamp: '8h ago',
    likes: 256,
    comments: 67,
    reposts: 34,
    liked: false,
  },
  {
    id: 'post-4',
    author: { name: 'David Kim', headline: 'CTO at NextGen Startup', avatar: 'https://i.pravatar.cc/150?u=david' },
    content: 'We\'re hiring! Looking for talented engineers who want to work on cutting-edge distributed systems. Fully remote, competitive comp, and a team that genuinely cares about engineering excellence.\n\nDM me or check out our careers page.',
    timestamp: '1d ago',
    likes: 312,
    comments: 89,
    reposts: 56,
    liked: false,
  },
  {
    id: 'post-5',
    author: { name: 'Emily Zhang', headline: 'Frontend Developer at PixelPerfect', avatar: 'https://i.pravatar.cc/150?u=emily' },
    content: 'TIL: React Server Components and Server Actions have completely changed how I think about data fetching. The mental model shift is real but worth it. No more useEffect waterfalls!\n\nAnyone else making the transition?',
    timestamp: '1d ago',
    likes: 178,
    comments: 52,
    reposts: 19,
    liked: false,
  },
]

const initialJobs = [
  { id: 'job-1', title: 'Senior Frontend Engineer', company: 'TechCorp', location: 'San Francisco, CA (Hybrid)', salary: '$160k - $200k', posted: '2 days ago', logo: 'https://i.pravatar.cc/40?u=techcorp', applicants: 47 },
  { id: 'job-2', title: 'Full Stack Developer', company: 'StartupXYZ', location: 'Remote', salary: '$130k - $170k', posted: '1 week ago', logo: 'https://i.pravatar.cc/40?u=startupxyz', applicants: 128 },
  { id: 'job-3', title: 'React Developer', company: 'PixelPerfect', location: 'New York, NY', salary: '$140k - $180k', posted: '3 days ago', logo: 'https://i.pravatar.cc/40?u=pixelperfect', applicants: 63 },
  { id: 'job-4', title: 'Staff Engineer', company: 'CloudScale', location: 'Seattle, WA (On-site)', salary: '$190k - $240k', posted: '5 days ago', logo: 'https://i.pravatar.cc/40?u=cloudscale', applicants: 92 },
  { id: 'job-5', title: 'Engineering Manager', company: 'DataFlow', location: 'Remote', salary: '$180k - $220k', posted: '1 day ago', logo: 'https://i.pravatar.cc/40?u=dataflow', applicants: 34 },
  { id: 'job-6', title: 'Junior Software Engineer', company: 'InnovateCo', location: 'Austin, TX (Hybrid)', salary: '$90k - $120k', posted: '4 days ago', logo: 'https://i.pravatar.cc/40?u=innovateco', applicants: 215 },
]

const initialMessages = [
  { id: 'msg-1', contact: { name: 'Sarah Chen', avatar: 'https://i.pravatar.cc/150?u=sarah' }, lastMessage: 'Thanks for the referral! I really appreciate it.', timestamp: '10:30 AM', unread: true },
  { id: 'msg-2', contact: { name: 'David Kim', avatar: 'https://i.pravatar.cc/150?u=david' }, lastMessage: 'Would love to chat about the role. Are you free this week?', timestamp: 'Yesterday', unread: true },
  { id: 'msg-3', contact: { name: 'Emily Zhang', avatar: 'https://i.pravatar.cc/150?u=emily' }, lastMessage: 'That React conf talk was amazing!', timestamp: 'Monday', unread: false },
  { id: 'msg-4', contact: { name: 'James O\'Brien', avatar: 'https://i.pravatar.cc/150?u=james' }, lastMessage: 'Let me know if you need anything else for the project.', timestamp: 'Apr 15', unread: false },
]

export function LinkedInProvider({ children }) {
  const [currentUser] = useState(initialCurrentUser)
  const [posts, setPosts] = useState(initialPosts)
  const [people] = useState(initialPeople)
  const [jobs] = useState(initialJobs)
  const [messages] = useState(initialMessages)
  const [connectedIds, setConnectedIds] = useState(new Set())
  const [savedJobIds, setSavedJobIds] = useState(new Set())

  function addPost(content) {
    const newPost = {
      id: `post-${Date.now()}`,
      author: { name: currentUser.name, headline: currentUser.headline, avatar: currentUser.avatar },
      content,
      timestamp: 'Just now',
      likes: 0,
      comments: 0,
      reposts: 0,
      liked: false,
    }
    setPosts([newPost, ...posts])
  }

  function toggleLike(postId) {
    setPosts(posts.map(post => {
      if (post.id === postId) {
        return { ...post, liked: !post.liked, likes: post.liked ? post.likes - 1 : post.likes + 1 }
      }
      return post
    }))
  }

  function toggleConnect(personId) {
    setConnectedIds(prev => {
      const next = new Set(prev)
      if (next.has(personId)) {
        next.delete(personId)
      } else {
        next.add(personId)
      }
      return next
    })
  }

  function toggleSaveJob(jobId) {
    setSavedJobIds(prev => {
      const next = new Set(prev)
      if (next.has(jobId)) {
        next.delete(jobId)
      } else {
        next.add(jobId)
      }
      return next
    })
  }

  const value = {
    currentUser,
    posts,
    people,
    jobs,
    messages,
    connectedIds,
    savedJobIds,
    addPost,
    toggleLike,
    toggleConnect,
    toggleSaveJob,
  }

  return (
    <LinkedInContext.Provider value={value}>
      {children}
    </LinkedInContext.Provider>
  )
}

export function useLinkedIn() {
  const context = useContext(LinkedInContext)
  if (!context) {
    throw new Error('useLinkedIn must be used within a LinkedInProvider')
  }
  return context
}
