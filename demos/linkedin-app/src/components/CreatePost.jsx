import { useState } from 'react'
import { useLinkedIn } from '../context/LinkedInContext'

function CreatePost() {
  const { currentUser, addPost } = useLinkedIn()
  const [content, setContent] = useState('')
  const [isExpanded, setIsExpanded] = useState(false)

  function handleSubmit(e) {
    e.preventDefault()
    if (content.trim()) {
      addPost(content.trim())
      setContent('')
      setIsExpanded(false)
    }
  }

  return (
    <div className="create-post">
      <div className="create-post-top">
        <img src={currentUser.avatar} alt={currentUser.name} className="create-post-avatar" />
        <button className="create-post-input" onClick={() => setIsExpanded(true)}>
          Start a post
        </button>
      </div>
      {isExpanded && (
        <form onSubmit={handleSubmit} className="create-post-form">
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder="What do you want to talk about?"
            rows={4}
            autoFocus
          />
          <div className="create-post-actions">
            <button type="button" className="create-post-cancel" onClick={() => { setIsExpanded(false); setContent('') }}>
              Cancel
            </button>
            <button type="submit" className="create-post-submit" disabled={!content.trim()}>
              Post
            </button>
          </div>
        </form>
      )}
    </div>
  )
}

export default CreatePost
