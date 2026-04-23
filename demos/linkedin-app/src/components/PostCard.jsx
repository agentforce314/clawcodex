import { useLinkedIn } from '../context/LinkedInContext'

function PostCard({ post }) {
  const { toggleLike } = useLinkedIn()

  return (
    <div className="post-card">
      <div className="post-header">
        <img src={post.author.avatar} alt={post.author.name} className="post-avatar" />
        <div className="post-author-info">
          <h4 className="post-author-name">{post.author.name}</h4>
          <p className="post-author-headline">{post.author.headline}</p>
          <span className="post-timestamp">{post.timestamp}</span>
        </div>
      </div>
      <div className="post-content">
        <p>{post.content}</p>
      </div>
      <div className="post-stats">
        <span>{post.likes > 0 && `${post.likes} likes`}</span>
        <span>
          {post.comments > 0 && `${post.comments} comments`}
          {post.reposts > 0 && ` \u00b7 ${post.reposts} reposts`}
        </span>
      </div>
      <div className="post-actions">
        <button className={`post-action-btn ${post.liked ? 'liked' : ''}`} onClick={() => toggleLike(post.id)}>
          <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
            <path d="M19.46 11l-3.91-3.91a7 7 0 01-1.69-2.74l-.49-1.47A2.76 2.76 0 0010.76 1 2.75 2.75 0 008 3.74v1.12a9.19 9.19 0 00.46 2.85L8.89 9H4.12A2.12 2.12 0 002 11.12a2.16 2.16 0 00.92 1.76A2.11 2.11 0 002 14.62a2.14 2.14 0 001.28 2 2 2 0 00-.28 1 2.12 2.12 0 002 2.12v.14A2.12 2.12 0 007.12 22h7.49a8.08 8.08 0 003.58-.84l.31-.16H21V11h-1.54zM19.5 19.5H19h.5z"/>
          </svg>
          <span>Like</span>
        </button>
        <button className="post-action-btn">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
            <path d="M7 9h10v1H7zm0 4h7v-1H7zm16-2a6.78 6.78 0 01-2.84 5.61L16 22v-4h-2A7 7 0 017 11a6.78 6.78 0 012.84-5.61L14 0v4h2a7 7 0 017 7z"/>
          </svg>
          <span>Comment</span>
        </button>
        <button className="post-action-btn">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
            <path d="M13.96 5H6c-1.1 0-2 .9-2 2v10l4-4h6c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm6 2h-2v6c0 1.1-.9 2-2 2H8v2c0 1.1.9 2 2 2h6l4 4V9c0-1.1-.9-2-2-2z"/>
          </svg>
          <span>Repost</span>
        </button>
        <button className="post-action-btn">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
            <path d="M21 3L0 10l7.66 4.26L16 8l-6.26 8.34L14 24l7-21z"/>
          </svg>
          <span>Send</span>
        </button>
      </div>
    </div>
  )
}

export default PostCard
