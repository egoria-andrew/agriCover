(() => {
  const form = document.getElementById('ussdForm');
  if (!form) return;
  const screen = document.getElementById('ussdText');
  const field = document.getElementById('ussdChoice');
  const error = document.getElementById('ussdError');
  const pages = {
    home: {text:'Welcome to AgriCover\nChoose a service:\n\n1. Crop cover\n2. Livestock cover\n3. Claim help\n4. Talk to an agent', next:['crop','livestock','claim','agent']},
    crop: {text:'WEATHER CROP COVER\nProposed cover for maize, beans and other crops.\n\nPremium and weather trigger are agreed with the insurer.\n\n0. Main menu', next:['home']},
    livestock: {text:'LIVESTOCK COVER\nProposed cover for livestock death.\n\nEligibility and benefits are agreed with the insurer.\n\n0. Main menu', next:['home']},
    claim: {text:'CLAIM SUPPORT\nAn agent helps record a loss. The insurer reviews the claim.\n\nNo AI or payout service is connected.\n\n0. Main menu', next:['home']},
    agent: {text:'AGENT SUPPORT\nA field agent can explain the terms, record consent and help with enrolment.\n\nThis preview is not connected to a telecom short code.\n\n0. Main menu', next:['home']}
  };
  let current = 'home';
  const render = () => { screen.textContent = pages[current].text; field.value = ''; error.textContent = ''; };
  form.addEventListener('submit', event => {
    event.preventDefault();
    const value = field.value.trim();
    const destination = value === '0' ? 'home' : pages[current].next[Number(value)-1];
    if (!destination) { error.textContent = 'Choose one of the numbers shown above.'; return; }
    current = destination;
    render();
  });
  document.getElementById('ussdRestart')?.addEventListener('click', () => { current = 'home'; render(); field.focus(); });
  render();
})();
