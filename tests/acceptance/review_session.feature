Feature: Review session

    The session runner is client-side: the page ships an empty shell and
    lute-review.js fills it from POST /review/start, then posts each
    grade.  A card that never renders looks exactly like a session with
    nothing due, so these scenarios read the live DOM rather than the
    markup.

    Keywords are explicit here because pytest-bdd matches a step only
    under the keyword it was registered with, and `I visit` is a Given
    throughout this suite.


    Background:
        Given a running site
        And demo languages


    Scenario: A learning term is auto-admitted, graded, and ungraded
        Given a new Spanish term:
            text: gato
            translation: cat
        And I visit "/review/index"
        Then the review dashboard shows 0 due cards
        Given I visit "/review/session"
        Then the review card shows the term "gato"
        # Nothing has been graded yet, so Undo must not be on screen at
        # all -- not merely flagged hidden in the markup.
        And the undo button is hidden
        When I reveal the review answer
        And I grade the card "Good"
        Then the review session is done
        And the undo button is available
        When I undo the last grade
        Then the review card shows the term "gato"


    Scenario: A card is pronounced in the language of its term
        Given a new Spanish term:
            text: gato
            translation: cat
        And I record what the review session pronounces
        Given I visit "/review/session"
        Then the review card offers pronunciation
        When I click the speaker on the review card
        # Not the browser's own language: the queue holds the terms of
        # every language the user studies, so each card says which one it
        # is spoken in.
        Then the term "gato" is spoken as "es-ES"


    Scenario: Opening a card pronounces its term
        Given a new Spanish term:
            text: gato
            translation: cat
        And I record what the review session pronounces
        Given I visit "/review/session"
        Then the review card shows the term "gato"
        # No click and no key press: the term is said as the card opens,
        # which is what "the card has TTS" means.  The page has been
        # interacted with by now (creating the term was a click, and
        # Chromium's activation is sticky across navigations), so the
        # browser lets it speak; the untouched page is the next
        # scenario.
        Then the term "gato" is spoken as "es-ES"


    Scenario: An untouched page pronounces the card at the first key press
        Given a new Spanish term:
            text: gato
            translation: cat
        And I record what the review session pronounces
        And the page has not been interacted with
        Given I visit "/review/session"
        Then the review card shows the term "gato"
        # Chrome and Safari drop speech requested before the document has
        # been activated, so the opening utterance is held back and
        # released by the first interaction rather than lost.
        And nothing was spoken
        When I press the space key in the review session
        Then the term "gato" is spoken as "es-ES"


    Scenario: Turning the pronunciation off keeps the cards quiet
        Given a new Spanish term:
            text: gato
            translation: cat
        And I record what the review session pronounces
        # Explicit keyword: a bare And inherits the previous one (Given),
        # and the step is registered as a When.
        When I turn off the card pronunciation in the review settings
        # Untouched as well, so the gesture that would release an owed
        # utterance is covered too: the switch has to silence the card
        # on both paths, not just the immediate one.
        Given the page has not been interacted with
        Given I visit "/review/session"
        Then the review card shows the term "gato"
        When I press the space key in the review session
        Then nothing was spoken
