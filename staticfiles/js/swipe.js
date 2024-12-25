document.addEventListener('DOMContentLoaded', function() {
    const swipeContainer = document.getElementById('swipe-container');
    const swipeCard = document.getElementById('swipeCard');
    const likeButton = document.getElementById('like-btn');
    const dislikeButton = document.getElementById('dislike-btn');
    const form = document.getElementById('swipe-form');
    const actionInput = form.querySelector('input[name="action"]');

    let hammer = null;
    let isSwipeInProgress = false;

    setupSwipe();

    function setupSwipe() {
        if (swipeCard) {
            if (hammer) {
                hammer.destroy();
            }
            hammer = new Hammer(swipeCard);
            hammer.get('pan').set({ direction: Hammer.DIRECTION_HORIZONTAL });
            hammer.on('pan', handlePan);
            hammer.on('panend', handlePanEnd);
            
            const profilePicture = swipeCard.querySelector('.card-img-top');
            if (profilePicture) {
                profilePicture.addEventListener('dragstart', (e) => e.preventDefault());
            }
        }
    }

    function handlePan(event) {
        if (isSwipeInProgress) return;
        
        const xPos = event.deltaX;
        const rotate = xPos / 10;

        swipeCard.style.transform = `translateX(${xPos}px) rotate(${rotate}deg)`;
        swipeCard.style.transition = 'none';
    }

    function handlePanEnd(event) {
        if (isSwipeInProgress) return;

        const threshold = 150;
        let action = 'reset';

        if (event.deltaX > threshold) {
            action = 'like';
        } else if (event.deltaX < -threshold) {
            action = 'dislike';
        }

        if (action === 'reset') {
            swipeCard.style.transform = '';
        } else {
            const endX = action === 'like' ? window.innerWidth : -window.innerWidth;
            swipeCard.style.transform = `translateX(${endX}px) rotate(${event.deltaX / 10}deg)`;
        }

        swipeCard.style.transition = 'transform 0.5s';

        if (action !== 'reset') {
            isSwipeInProgress = true;
            setTimeout(() => {
                submitForm(action);
                isSwipeInProgress = false;
            }, 500);
        }
    }

    function submitForm(action) {
        actionInput.value = action;
        form.submit();
    }

    if (likeButton) {
        likeButton.addEventListener('click', (e) => {
            e.preventDefault();
            submitForm('like');
        });
    }

    if (dislikeButton) {
        dislikeButton.addEventListener('click', (e) => {
            e.preventDefault();
            submitForm('dislike');
        });
    }

    function showMatchView(matchProfile, nextProfile) {
        if (!matchProfile) {
            console.error('No match profile data provided to showMatchView');
            return;
        }
    
        console.log('Match profile data:', matchProfile);
        console.log('Current user picture:', currentUserPicture);
    
        const matchView = document.createElement('div');
        matchView.className = 'match-view';
        matchView.innerHTML = `
            <div class="match-content">
                <h2>It's a Match!</h2>
                <div class="match-profiles">
                    <img src="${currentUserPicture}" alt="Your profile" class="match-profile-pic">
                    <img src="${matchProfile.profile_picture}" alt="${matchProfile.full_name}'s profile" class="match-profile-pic">
                </div>
                <p>You and ${matchProfile.full_name} have liked each other!</p>
                <button id="continueSwipingBtn" class="btn btn-primary">Continue Swiping</button>
            </div>
        `;
        
        swipeContainer.innerHTML = '';
        swipeContainer.appendChild(matchView);
        if (buttonsContainer) buttonsContainer.style.display = 'none';
    
        document.getElementById('continueSwipingBtn').addEventListener('click', () => {
            if (nextProfile) {
                updateProfileCard(nextProfile);
            } else {
                redirectToNoMoreProfiles();
            }
        });
    }

    function updateProfileCard(profile) {
        if (!profile) {
            console.error('No profile data provided to updateProfileCard');
            showErrorModal('An error occurred while loading the next profile. Please try again.');
            return;
        }

        const profilePicture = profile.profile_picture ? profile.profile_picture : '/static/images/default-profile.png';
        const newCard = document.createElement('div');
        newCard.className = 'profile-card';
        newCard.dataset.profileId = profile.id;
        newCard.innerHTML = `
            <img src="${profilePicture}" alt="${profile.full_name}'s profile picture" class="profile-picture">
            <div class="profile-info">
                <h2>${profile.full_name}</h2>
                <p><strong>Industry:</strong> ${profile.industry || 'N/A'}</p>
                <p><strong>Company:</strong> ${profile.company || 'N/A'}</p>
                <p>${profile.bio || 'No bio available.'}</p>
            </div>
        `;
        
        swipeContainer.innerHTML = '';
        swipeContainer.appendChild(newCard);
        if (buttonsContainer) buttonsContainer.style.display = 'flex';

        currentCard = newCard;
        setupSwipe();
    }

    function showNoMoreProfiles() {
        swipeContainer.innerHTML = `
            <div class="profile-card">
                <div class="profile-info text-center">
                    <h2>No more profiles</h2>
                    <p>Check back later for new matches!</p>
                </div>
            </div>
        `;
        if (buttonsContainer) buttonsContainer.style.display = 'none';
    }

    function showErrorModal(message) {
        const modal = document.createElement('div');
        modal.className = 'error-modal';
        modal.innerHTML = `
            <div class="error-content">
                <h3>Error</h3>
                <p>${message}</p>
                <button onclick="this.closest('.error-modal').remove()">Close</button>
            </div>
        `;
        document.body.appendChild(modal);
    }

    function getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }

    function redirectToNoMoreProfiles() {
        window.location.href = '/no-more-profiles/';  // Adjust this URL if necessary
    }
    
    if (likeButton) likeButton.addEventListener('click', () => swipeAction('like'));
    if (dislikeButton) dislikeButton.addEventListener('click', () => swipeAction('dislike'));
});