// Global site script extensions
(function(){
  // Subnav dropdown toggle
  function setupSubnavDropdown(){
    var dropdowns = document.querySelectorAll('[data-dropdown]');
    dropdowns.forEach(function(dd){
      var btn = dd.querySelector('.subnav-trigger');
      var menu = dd.querySelector('.subnav-menu');
      if(!btn || !menu) return;
      btn.addEventListener('click', function(){
        var expanded = btn.getAttribute('aria-expanded') === 'true';
        btn.setAttribute('aria-expanded', (!expanded).toString());
        dd.classList.toggle('open');
      });
      document.addEventListener('click', function(e){
        if(!dd.contains(e.target)){
          dd.classList.remove('open');
          btn.setAttribute('aria-expanded', 'false');
        }
      });
    });
  }

  // Year filter for schedule page
  function setupYearFilter(){
    var select = document.getElementById('yearSelect');
    var list = document.getElementById('schedule-list');
    if(!select || !list) return;
    select.addEventListener('change', function(){
      var year = select.value;
      var items = list.querySelectorAll('li[data-year]');
      items.forEach(function(item){
        item.style.display = (item.getAttribute('data-year') === year) ? '' : 'none';
      });
    });
    // trigger initial filter
    var event = new Event('change');
    select.dispatchEvent(event);
  }

  // Expose language function if missing
  if(typeof window.setLanguage !== 'function'){
    window.setLanguage = function(lang){
      // placeholder: could store in localStorage, update text, etc.
      console.log('Language switched to:', lang);
    };
  }

  // Image Modal Functions
  window.openImageModal = function(imageSrc) {
    var modal = document.getElementById('imageModal');
    var modalImg = document.getElementById('modalImage');
    var caption = document.getElementById('modalCaption');
    
    modal.style.display = 'block';
    modalImg.src = imageSrc;
    caption.innerHTML = modalImg.alt || '';
    
    // Prevent body scroll when modal is open
    document.body.style.overflow = 'hidden';
  };

  window.closeImageModal = function() {
    var modal = document.getElementById('imageModal');
    modal.style.display = 'none';
    
    // Restore body scroll
    document.body.style.overflow = 'auto';
  };

  // Change Main Image Function for Show galleries
  window.changeMainImage = function(showType, imageSrc) {
    var mainImage = document.getElementById(showType + '-main-image');
    if (mainImage) {
      // Fade out
      mainImage.style.opacity = '0';
      
      // Change image and fade in
      setTimeout(function() {
        mainImage.src = imageSrc;
        mainImage.style.opacity = '1';
        
        // Smooth scroll to the main image
        mainImage.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, 300);
    }
  };

  // Close modal when clicking outside the image
  document.addEventListener('click', function(e) {
    var modal = document.getElementById('imageModal');
    if (e.target === modal) {
      closeImageModal();
    }
  });

  // Close modal with ESC key
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
      closeImageModal();
    }
  });

  document.addEventListener('DOMContentLoaded', function(){
    setupSubnavDropdown();
    setupYearFilter();
  });
})();
