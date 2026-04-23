import { useLinkedIn } from '../context/LinkedInContext'
import ProfileCard from '../components/ProfileCard'
import CreatePost from '../components/CreatePost'
import PostCard from '../components/PostCard'

function Feed() {
  const { posts } = useLinkedIn()

  return (
    <div className="feed-layout">
      <aside className="feed-sidebar-left">
        <ProfileCard />
      </aside>
      <section className="feed-main">
        <CreatePost />
        {posts.map(post => (
          <PostCard key={post.id} post={post} />
        ))}
      </section>
      <aside className="feed-sidebar-right">
        <div className="trending-card">
          <h3>LinkedIn News</h3>
          <ul className="trending-list">
            <li>
              <h4>Tech layoffs slow in Q2</h4>
              <span>4h ago &middot; 12,847 readers</span>
            </li>
            <li>
              <h4>AI skills demand surges 300%</h4>
              <span>6h ago &middot; 8,293 readers</span>
            </li>
            <li>
              <h4>Remote work debate continues</h4>
              <span>1d ago &middot; 24,512 readers</span>
            </li>
            <li>
              <h4>New startup funding records</h4>
              <span>2d ago &middot; 5,671 readers</span>
            </li>
            <li>
              <h4>Developer salaries on the rise</h4>
              <span>3d ago &middot; 18,943 readers</span>
            </li>
          </ul>
        </div>
      </aside>
    </div>
  )
}

export default Feed
