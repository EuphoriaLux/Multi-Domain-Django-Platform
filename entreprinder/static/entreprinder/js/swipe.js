/**
 * Escape HTML to prevent XSS attacks
 */
function escapeHtml(text) {
    if (text === null || text === undefined) return '';
    const str = String(text);
    return str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#x27;');
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

function submitForm(action) {
    var form = document.getElementById('swipe-form');
    var entrepreneurId = document.getElementById('id_entrepreneur_id').value;
    
    // Get the current URL path
    var currentPath = window.location.pathname;
    // Extract the language code (assuming it's always the first part of the path)
    var languageCode = currentPath.split('/')[1];
    // Construct the URL for the AJAX call
    var ajaxUrl = '/' + languageCode + '/matching/swipe/action/';

    fetch(ajaxUrl, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCookie('csrftoken')
        },
        body: JSON.stringify({
            action: action,
            profile_id: entrepreneurId
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.match_found) {
            showMatchPopup(data.match_profile);
        }
        if (data.next_profile) {
            updateProfile(data.next_profile);
            resetCard();
        } else if (data.redirect_url) {
            window.location.href = data.redirect_url;
        } else if (data.no_more_profiles) {
            document.getElementById('swipeCard').style.display = 'none';
            document.querySelector('.swipe-buttons').style.display = 'none';
            document.querySelector('#swipe-container').innerHTML = '<p class="text-center">No more profiles to show.</p>';
        } else {
            console.log("Profile already liked/disliked, loading next profile if available");
             if (data.next_profile) {
                updateProfile(data.next_profile);
                resetCard();
            } else {
                document.getElementById('swipeCard').style.display = 'none';
                document.querySelector('.swipe-buttons').style.display = 'none';
                document.querySelector('#swipe-container').innerHTML = '<p class="text-center">No more profiles to show.</p>';
            }
        }
    })
    .catch(error => console.error('Error:', error));
}

function resetCard() {
    var swipeCard = document.getElementById('swipeCard');
    var likeOverlay = swipeCard.querySelector('.swipe-overlay.like');
    var dislikeOverlay = swipeCard.querySelector('.swipe-overlay.dislike');

    swipeCard.style.transition = 'none';
    swipeCard.style.transform = 'translateX(0) rotate(0)';
    likeOverlay.style.opacity = 0;
    dislikeOverlay.style.opacity = 0;
}

function showMatchPopup(matchProfile) {
    // Safely set image source (validate against all dangerous URL schemes)
    const imgEl = document.getElementById('matchProfilePicture');
    const profilePic = matchProfile.profile_picture || '';
    const lowerPic = profilePic.toLowerCase();
    if (lowerPic.startsWith('javascript:') || lowerPic.startsWith('data:') || lowerPic.startsWith('vbscript:')) {
        imgEl.src = ''; // Block dangerous URLs
    } else {
        imgEl.src = profilePic;
    }

    // Safely set text content (prevents XSS)
    document.getElementById('matchName').textContent = matchProfile.full_name || '';
    document.getElementById('matchPopup').style.display = 'block';
}

function closeMatchPopup() {
    document.getElementById('matchPopup').style.display = 'none';
}

function updateProfile(profile) {
    // Safely set image source (validate against all dangerous URL schemes)
    const imgEl = document.querySelector('.card-img-top');
    const profilePic = profile.profile_picture || '';
    const lowerPic = profilePic.toLowerCase();
    if (lowerPic.startsWith('javascript:') || lowerPic.startsWith('data:') || lowerPic.startsWith('vbscript:')) {
        imgEl.src = ''; // Block dangerous URLs
    } else {
        imgEl.src = profilePic;
    }

    // Safely set text content (prevents XSS)
    document.querySelector('.card-title').textContent = profile.full_name || '';

    // Build profile info safely using DOM methods instead of innerHTML
    const profileInfoEl = document.querySelector('.profile-info');
    profileInfoEl.textContent = ''; // Clear existing content

    const companyP = document.createElement('p');
    companyP.innerHTML = '<strong>Company:</strong> ';
    companyP.appendChild(document.createTextNode(profile.company || 'N/A'));
    profileInfoEl.appendChild(companyP);

    const industryP = document.createElement('p');
    industryP.innerHTML = '<strong>Industry:</strong> ';
    industryP.appendChild(document.createTextNode(profile.industry || 'N/A'));
    profileInfoEl.appendChild(industryP);

    document.querySelector('.bio').textContent = profile.bio || '';
    document.getElementById('id_entrepreneur_id').value = profile.id || '';
}

document.addEventListener('DOMContentLoaded', function() {
    var swipeCard = document.getElementById('swipeCard');
    if (swipeCard) {
        var hammer = new Hammer(swipeCard);
        var likeOverlay = swipeCard.querySelector('.swipe-overlay.like');
        var dislikeOverlay = swipeCard.querySelector('.swipe-overlay.dislike');

        hammer.on('panmove', function(e) {
            var deltaX = e.deltaX;
            var opacity = Math.abs(deltaX) / 100;
            swipeCard.style.transform = `translateX(${deltaX}px) rotate(${deltaX / 10}deg)`;
            
            if (deltaX > 0) {
                likeOverlay.style.opacity = opacity;
                dislikeOverlay.style.opacity = 0;
            } else {
                dislikeOverlay.style.opacity = opacity;
                likeOverlay.style.opacity = 0;
            }
        });

        hammer.on('panend', function(e) {
            var deltaX = e.deltaX;
            var direction = deltaX > 0 ? 'like' : 'dislike';
            
            if (Math.abs(deltaX) > 100) {
                swipeCard.style.transition = 'transform 0.3s ease-out';
                swipeCard.style.transform = `translateX(${deltaX > 0 ? 1000 : -1000}px) rotate(${deltaX / 10}deg)`;
                setTimeout(() => {
                    swipeCard.style.transition = 'none';
                    submitForm(direction);
                    swipeCard.style.transform = '';
                    likeOverlay.style.opacity = 0;
                    dislikeOverlay.style.opacity = 0;
                }, 300);
            } else {
                swipeCard.style.transition = 'transform 0.3s ease-out';
                swipeCard.style.transform = 'translateX(0) rotate(0)';
                setTimeout(() => {
                    swipeCard.style.transition = 'none';
                    likeOverlay.style.opacity = 0;
                    dislikeOverlay.style.opacity = 0;
                }, 300);
            }
        });
    }

    var likeBtn = document.getElementById('like-btn');
    var dislikeBtn = document.getElementById('dislike-btn');

    if (likeBtn) {
        likeBtn.addEventListener('click', function(e) {
            e.preventDefault();
            submitForm('like');
        });
    }

    if (dislikeBtn) {
        dislikeBtn.addEventListener('click', function(e) {
            e.preventDefault();
            submitForm('dislike');
        });
    }
});
